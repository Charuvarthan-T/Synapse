import hashlib
from app.db import Database


class User:
    def __init__(self, name, password_hash):
        self.name = name
        self.password_hash = password_hash


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def login(db: Database, name, password):
    user = db.find_user(name)
    if user and user.password_hash == hash_password(password):
        return create_session(user)
    return None


def create_session(user):
    return {"user": user.name}
