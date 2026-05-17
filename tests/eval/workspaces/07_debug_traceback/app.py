def parse_age(value: str) -> int:
    age = int(value)
    if age < 0:
        raise ValueError("age must be positive")
    return age
