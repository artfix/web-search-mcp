from .keystore import load_all_env_files
from .server import run


def main() -> None:
    # Make SEARCH_MCP_* keys in ./.env AND <config_dir>/.env visible to the
    # keyed engines (keystore reads os.environ, which pydantic's .env loading
    # doesn't populate). The config-dir file covers uvx launches from any CWD.
    load_all_env_files()
    run()


if __name__ == "__main__":
    main()
