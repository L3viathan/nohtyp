import dis
import glob
import marshal
import logging
import builtins
import py_compile
from dataclasses import dataclass

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

opmap = {code: name for name, code in dis.opmap.items()}
mod_cache = {}


def pairwise(iterable):
    iterator = iter(iterable)
    for first in iterator:
        yield first, next(iterator)


class Namespace:
    def __init__(self, name, mappings=None, parent=None):
        self.__parent = parent
        self.__name__ = name
        if mappings:
            self.__dict__.update(**mappings)

    def __getattr__(self, attribute):
        if self.__parent:
            log.warn(f"Can't find {attribute} on {self.__name__}, looking at parent ({self.__parent.__name__})")
            return getattr(self.__parent, attribute)
        raise AttributeError(f"Can't find {attribute} on {self.__name__}")


class Python:
    def __init__(self, code, my_name="unknown", mappings=None, module=None):
        for k in dir(code):
            if k.startswith("co_"):
                _, __, name = k.partition("_")
                value = getattr(code, k)
                setattr(self, name, value)
        self._type = type
        self._stack = []
        self.varnames = list(self.varnames)
        if module:  # meaning self is _not_ a module
            # warning: module can be either real, builtin module, or own module
            self._mappings = Namespace(
                my_name, parent=module._mappings, mappings=mappings
            )
        else:
            self._mappings = Namespace(my_name, parent=builtins)
            mod_cache[my_name] = self
        self._module = module or self
        self._return = None
        self.ip = 0

    def __call__(self, *args):
        log.debug("Trying to run... (with args: %r)", args)
        for name, arg in zip(self.varnames, args):
            setattr(self._mappings, name, arg)
        self._return = self._mappings
        while True:
            code = self.code[self.ip : self.ip + 2]
            if not code:
                break
            opcode, arg = code
            self.ip += 2
            op = opmap[opcode]
            log.debug("opcode: %s, arg: %r", op, arg)
            getattr(self, op)(arg)
            log.debug("new stack: %r", self._stack)
        return self._return

    def LOAD_CONST(self, arg):
        self._stack.append(self.consts[arg])

    def MAKE_FUNCTION(self, arg):
        name = self._stack.pop()
        code = self._stack.pop()
        func = Function(code, self._mappings.__dict__, name=name)
        if arg & 0x08:
            func.__closure__ = self._stack.pop()
        if arg & 0x04:
            func.__annotations__ = self._stack.pop()
        if arg & 0x02:
            func.kwdefaults__ = self._stack.pop()
        if arg & 0x01:
            func.__defaults__ = self._stack.pop()
        self._stack.append(func)

    def STORE_NAME(self, arg):
        value = self._stack.pop()
        name = self.names[arg]
        setattr(self._mappings, name, value)

    def LOAD_NAME(self, arg):
        name = self.names[arg]
        self._stack.append(getattr(self._mappings, name))

    def CALL_FUNCTION(self, arg):
        args = reversed([self._stack.pop() for _ in range(arg)])
        function = self._stack.pop()
        log.info("Calling function %s", function.__name__)
        if isinstance(function, (type(print), type)):
            self._stack.append(function(*args))
        else:
            mapping = {}
            for key, val in zip(function.__code__.co_cellvars, function.__defaults__):
                mapping[key] = val
            # mapping.update(function.__globals__)
            import ipdb; ipdb.set_trace()
            self._stack.append(
                Python(
                    function.__code__,
                    function.__name__,
                    mappings=mapping,
                    # module=self._module,
                    module=mod_cache[function.__module__]
                )(*args)
            )

    def LOAD_FAST(self, arg):
        self._stack.append(getattr(self._mappings, self.varnames[arg]))

    def BINARY_ADD(self, arg):
        self._stack.append(self._stack.pop() + self._stack.pop())

    def RETURN_VALUE(self, arg):
        value = self._stack.pop()
        self._return = value

    def POP_TOP(self, arg):
        self._stack.pop()

    def IMPORT_NAME(self, arg):
        name = self.names[arg]
        if name in mod_cache:
            return self._stack.append(mod_cache[name])
        try:
            # TODO: module cache (for globals in modules)
            module = run(name)
        except FileNotFoundError:
            module = __import__(name)
        mod_cache[name] = module
        self._stack.append(module)

    def IMPORT_FROM(self, arg):
        name = self.names[arg]
        module = self._stack.pop()
        self._stack.append(module)
        self._stack.append(getattr(module, name))
        # setattr(self._mappings, name, getattr(module, name))

    def LOAD_ATTR(self, arg):
        attr = self.names[arg]
        obj = self._stack.pop()
        self._stack.append(getattr(obj, attr))

    def CALL_FUNCTION_KW(self, arg):
        keys = self._stack.pop()
        bindings = {key: self._stack.pop() for key in keys}
        func = self._stack.pop()
        self._stack.append(func(**bindings))

    def LOAD_METHOD(self, arg):
        name = self.names[arg]
        obj = self._stack.pop()
        # FIXME: this is actually supposed to push the _function_, not the method.
        method = getattr(obj, name)
        self._stack.append(method)
        self._stack.append(obj)

    def CALL_METHOD(self, arg):
        args = [self._stack.pop() for _ in range(arg)]
        obj = self._stack.pop()
        method = self._stack.pop()
        self._stack.append(method(*args))

    def GET_ITER(self, arg):
        self._stack.append(iter(self._stack.pop()))

    def BUILD_MAP(self, arg):
        self._stack.append(
            {
                key: value
                for key, value in pairwise(self._stack.pop() for _ in range(2 * arg))
            }
        )

    def FOR_ITER(self, arg):
        iterator = self._stack.pop()
        try:
            value = iterator.__next__()
            self._stack.append(iterator)
            self._stack.append(value)
        except StopIteration:
            self.ip += arg

    def UNPACK_SEQUENCE(self, arg):
        sequence = self._stack.pop()
        for val in reversed(sequence):
            self._stack.append(val)

    def STORE_FAST(self, arg):
        setattr(self._mappings, self.varnames[arg], self._stack.pop())

    def MAP_ADD(self, arg):
        value = self._stack.pop()
        key = self._stack.pop()
        self._stack[-arg][key] = value

    def JUMP_ABSOLUTE(self, arg):
        self.ip = arg

    def LOAD_BUILD_CLASS(self, arg):
        self._stack.append(builtins.__build_class__)

    def COMPARE_OP(self, arg):
        op = dis.cmp_op[arg]
        y, x = self._stack.pop(), self._stack.pop()
        if op == "==":
            self._stack.append(x == y)
        elif op == "<":
            self._stack.append(x < y)
        elif op == ">":
            self._stack.append(x > y)
        elif op == "<=":
            self._stack.append(x <= y)
        elif op == ">=":
            self._stack.append(x >= y)
        elif op == "!=":
            self._stack.append(x != y)
        elif op == "in":
            self._stack.append(x in y)
        elif op == "not in":
            self._stack.append(x not in y)
        elif op == "is":
            self._stack.append(x is y)
        elif op == "is not":
            self._stack.append(x is not y)
        elif op == "exception match":
            import ipdb

            ipdb.set_trace()
            ...
        else:
            raise RuntimeError("This probably shouldn't happen")

    def POP_JUMP_IF_FALSE(self, arg):
        if not self._stack.pop():
            self.ip = arg

    def POP_JUMP_IF_TRUE(self, arg):
        if self._stack.pop():
            self.ip = arg

    def LOAD_GLOBAL(self, arg):
        self._stack.append(getattr(self._mappings, self.names[arg]))

    def FORMAT_VALUE(self, arg):
        if (arg & 0x04) == 0x04:
            fmt_spec = self._stack.pop()
        else:
            fmt_spec = ""
        value = self._stack.pop()
        if (arg & 0x03) == 0x01:
            value = str(value)
        elif (arg & 0x03) == 0x02:
            value = repr(value)
        elif (arg & 0x03) == 0x03:
            value = ascii(value)
        self._stack.append(value.__format__(fmt_spec))

    def BUILD_STRING(self, arg):
        self._stack.append("".join(self._stack.pop() for _ in range(arg)))

    def LOAD_DEREF(self, arg):
        self._stack.append(getattr(self._mappings, self.cellvars[arg]))

    def JUMP_IF_TRUE_OR_POP(self, arg):
        val = self._stack.pop()
        if val:
            self.ip.seek(arg)
            self._stack.append(val)

    def STORE_DEREF(self, arg):
        setattr(self._mappings, self.cellvars[arg], self._stack.pop())


Function = type(pairwise)


def run(filename, try_compile=True, is_main=False):
    log.debug("trying to run %s", filename)
    cached = glob.glob(f"__pycache__/{filename}.cpython-*.pyc")
    if not cached:
        if try_compile:
            log.debug("Attempting to compile file")
            py_compile.compile(f"{filename}.py")
            return run(filename, try_compile=False)
        raise FileNotFoundError("Couldn't find cached file")
    cached = cached[0]
    log.debug("Found cached file %s", cached)
    with open(cached, "rb") as f:
        f.seek(16)  # ignore magic + timestamp
        code = marshal.load(f)
        interpreter = Python(code, "__main__" if is_main else filename)
        return interpreter()


if __name__ == "__main__":
    run("curry", is_main=True)
