import dis
import glob
import marshal
import logging
from dataclasses import dataclass

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

opmap = {code: name for name, code in dis.opmap.items()}


def pairwise(iterable):
    iterator = iter(iterable)
    for first in iterator:
        yield first, next(iterator)


class Module:
    def __init__(self, name):
        self.__name__ = name
        self.print = print


class Python:
    def __init__(self, code, methodname="unknown"):
        for k in dir(code):
            if k.startswith("co_"):
                _, __, name = k.partition("_")
                value = getattr(code, k)
                setattr(self, name, value)
        self._stack = []
        self.consts = [
            Python(item) if isinstance(item, type(code)) else item
            for item in self.consts
        ]
        self.varnames = list(self.varnames)
        self._mappings = Module(methodname)
        self._return = None

    def __call__(self, *args):
        log.debug("Trying to run... (with args: %r)", args)
        for name, arg in zip(self.varnames, args):
            setattr(self._mappings, name, arg)
        # FIXME: instead of dictionary, return attributed object
        self._return = self._mappings
        for opcode, arg in pairwise(self.code):
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
        self._stack.append(Function(name, code))

    def STORE_NAME(self, arg):
        value = self._stack.pop()
        name = self.names[arg]
        setattr(self._mappings, name, value)

    def LOAD_NAME(self, arg):
        name = self.names[arg]
        self._stack.append(getattr(self._mappings, name))

    def CALL_FUNCTION(self, arg):
        args = [self._stack.pop() for _ in range(arg)]
        function = self._stack.pop()
        self._stack.append(function(*args))

    def LOAD_FAST(self, arg):
        # FIXME: load from bindings?
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
        try:
            self._stack.append(run(name))
            # setattr(self._mappings, name, run(name))
        except FileNotFoundError:
            # FIXME: Either not compiled yet, or builtin. For now, just import the actual Python names
            # setattr(self._mappings, name, __import__(name))
            self._stack.append(__import__(name))

    def IMPORT_FROM(self, arg):
        # name = self.names[arg]
        module = self._stack.pop()
        names = self._stack.pop()
        for name in names:
            setattr(self._mappings, name, getattr(module, name))

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
        value = iterator.__next__()
        # FIXME: handle loop termination (advance bytecode by arg)
        self._stack.append(iterator)
        self._stack.append(value)

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
        ...


@dataclass
class Function:
    name: str
    code: Python

    def __call__(self, *args):
        return self.code(*args)


def run(filename):
    log.debug("trying to run %s", filename)
    cached = glob.glob(f"__pycache__/{filename}.cpython-*.pyc")
    if not cached:
        raise FileNotFoundError("Couldn't find cached file")
    cached = cached[0]
    log.debug("Found cached file %s", cached)
    with open(cached, "rb") as f:
        f.seek(16)  # ignore magic + timestamp
        code = marshal.load(f)
        interpreter = Python(code, filename)
        return interpreter()


if __name__ == "__main__":
    run("nohtyp")
