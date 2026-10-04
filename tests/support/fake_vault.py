"""In-memory stand-in for the secure credential vault."""


class Vault:
    def __init__(self):
        self.data = {}

    def get_password(self, service, account):
        return self.data.get((service, account))

    def set_password(self, service, account, secret):
        self.data[(service, account)] = secret

    def delete_password(self, service, account):
        del self.data[(service, account)]
