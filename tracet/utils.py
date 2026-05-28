import threading

def truthy(val) -> bool:
    # Bool
    if isinstance(val, bool):
        return val

    # Int/Floats 0 -> False, rest -> True
    try:
        return bool(float(val))
    except (ValueError, TypeError):
        pass

    # String values
    if isinstance(val, str):
        string = val.strip().lower()
        if string in ("0", "false", "no"):
            return False
        elif string in ("1", "true", "yes"):
            return True

    raise ValueError(f"Invalid boolean value: '{val}'")


class ThreadsafeBool:
    def __init__(self, val: bool):
        self.lock = threading.RLock()
        self(val)

    def __bool__(self) -> bool:
        with self.lock:
            return self.val

    def __call__(self, val: bool) -> bool:
        with self.lock:
            self.val = val
            return val