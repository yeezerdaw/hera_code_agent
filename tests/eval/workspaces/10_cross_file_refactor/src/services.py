from models import User


def build_user(name: str) -> User:
    return User(name)
