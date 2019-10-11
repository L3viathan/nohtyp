import dis
import glob
import marshal
from dataclasses import dataclass

opmap = {code: name for name, code in dis.opmap.items()}


def pairwise(iterable):
    iterator = iter(iterable)
    for first in iterator:
        yield first, next(iterator)


class Python:
    def __init__(self, code):
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
        self._mappings = {"print": print}
        self._return = None

    def __call__(self, *args):
        # print("Trying to run... (with args:", args, ")")
        for name, arg in zip(self.varnames, args):
            self._mappings[name] = arg
        self._return = None
        for opcode, arg in pairwise(self.code):
            op = opmap[opcode]
            # print("opcode:", op, "arg:", arg)
            getattr(self, op)(arg)
            # print("new stack:", self._stack)
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
        self._mappings[name] = value

    def LOAD_NAME(self, arg):
        name = self.names[arg]
        self._stack.append(self._mappings[name])

    def CALL_FUNCTION(self, arg):
        args = [self._stack.pop() for _ in range(arg)]
        function = self._stack.pop()
        self._stack.append(function(*args))

    def LOAD_FAST(self, arg):
        # FIXME: load from bindings?
        self._stack.append(self._mappings[self.varnames[arg]])

    def BINARY_ADD(self, arg):
        self._stack.append(self._stack.pop() + self._stack.pop())

    def RETURN_VALUE(self, arg):
        value = self._stack.pop()
        self._return = value

    def POP_TOP(self, arg):
        self._stack.pop()


@dataclass
class Function:
    name: str
    code: Python

    def __call__(self, *args):
        return self.code(*args)


def run(filename):
    # print("trying to run", filename)
    cached = glob.glob(f"__pycache__/{filename}.cpython-*.pyc")
    if not cached:
        print("Couldn't find cached file")
        return
    cached = cached[0]
    # print("Found cached file", cached)
    with open(cached, "rb") as f:
        f.seek(16)  # ignore magic + timestamp
        code = marshal.load(f)
        interpreter = Python(code)
        interpreter()


if __name__ == "__main__":
    run("simple")
