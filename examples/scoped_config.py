from confluid import configurable, load


@configurable
class Database:
    def __init__(self, host: str = "localhost", port: int = 5432, timeout: int = 30):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"Database(host='{self.host}', port={self.port}, timeout={self.timeout})"


# YAML with scoped overlays
config_yaml = """
scope_aliases:
  dev: [debug, local]

Database:
  host: "prod-db"
  port: 5432

debug:
  Database:
    host: "localhost"
    timeout: 1

local:
  Database:
    port: 8888

not debug:
  Database:
    timeout: 60
"""


def main() -> None:
    print("--- Default (No Scopes) ---")
    db_default = load(config_yaml)
    # 'not debug' applies, so timeout=60
    print(db_default)

    print("\n--- Debug Mode ---")
    db_debug = load(config_yaml, scopes=["debug"])
    # 'debug' applies, host=localhost, timeout=1
    print(db_debug)

    print("\n--- Dev Alias (Debug + Local) ---")
    db_dev = load(config_yaml, scopes=["dev"])
    # Both 'debug' and 'local' apply. port=8888 from local.
    print(db_dev)


if __name__ == "__main__":
    main()
