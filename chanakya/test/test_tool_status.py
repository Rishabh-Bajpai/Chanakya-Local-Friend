from chanakya.services.tool_loader import initialize_all_tools, get_cached_tools, get_tools_availability

def main() -> None:
    initialize_all_tools()
    print("Tools loaded:", len(get_cached_tools()))
    print("Status:", get_tools_availability())


if __name__ == "__main__":
    main()
