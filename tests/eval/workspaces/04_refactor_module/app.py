def authenticate(token: str) -> bool:
    return token == "secret-token"


def get_profile(token: str) -> dict:
    if not authenticate(token):
        raise PermissionError("invalid token")
    return {"id": 1, "name": "Ada"}
