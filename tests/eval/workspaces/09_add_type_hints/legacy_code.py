def normalize_name(name):
    return name.strip().title()


def make_user(name, age):
    return {"name": normalize_name(name), "age": age}
