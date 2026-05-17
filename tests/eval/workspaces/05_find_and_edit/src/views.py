from models import User


def render_user(name: str, email: str) -> str:
    user = User(name, email)
    return f"{user.name} <{user.email}>"
