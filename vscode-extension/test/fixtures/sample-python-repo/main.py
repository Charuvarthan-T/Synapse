def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"


class Greeter:
    def __init__(self, name: str) -> None:
        self.name = name

    def say_hello(self) -> str:
        return greet(self.name)


if __name__ == "__main__":
    print(Greeter("world").say_hello())
