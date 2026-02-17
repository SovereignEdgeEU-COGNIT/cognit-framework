from biscuit_auth import Authorizer, Biscuit, BiscuitBuilder, KeyPair
from datetime import datetime, timedelta, timezone

keypair = KeyPair()
PUBLIC_KEY = keypair.public_key.to_hex()

def generate_token(username: str, password: str, private_key=keypair.private_key) -> str:

    token = BiscuitBuilder("""
    user({name});
    password({password});
    check if time($time), $time < {expiration};
    """,
                           {
                               'name': username,
                               'password': password,
                               'expiration': datetime.now(tz=timezone.utc) + timedelta(days=1)
                           }).build(private_key)
    return token.to_base64()


def authorize_token(token64: str, public_key=keypair.public_key) -> str:
    token = Biscuit.from_base64(token64, public_key)

    authorizer = Authorizer("""
    time({now});
    allow if user($u), password($p);
    """,
                            {
                                'now': datetime.now(tz=timezone.utc)
                            })
    authorizer.add_token(token)
    authorizer.authorize()

    return token.block_source(0)


