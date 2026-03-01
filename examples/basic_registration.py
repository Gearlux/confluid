from confluid import configurable, get_registry, register


# 1. Using @configurable decorator
@configurable
class MyModel:
    def __init__(self, layers: int = 3):
        self.layers = layers


# 2. Registering a third-party class
class ExternalOptimizer:
    def __init__(self, lr: float = 0.01):
        self.lr = lr


register(ExternalOptimizer, name="Optimizer")

if __name__ == "__main__":
    print("Registered Classes:")
    for cls_name in get_registry().list_classes():
        print(f" - {cls_name}")

    # Instantiate
    model = MyModel()
    print(f"Model layers: {model.layers}")
