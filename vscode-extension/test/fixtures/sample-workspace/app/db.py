class Database:
    def __init__(self):
        self.users = {}

    def find_user(self, name):
        return self.users.get(name)

    def add_user(self, user):
        self.users[user.name] = user
