import os
import sys

def test_imports():
    print("Testing imports...")
    try:
        import google.generativeai as genai
        print("SUCCESS: google.generativeai imported.")
    except ImportError as e:
        print(f"FAILURE: Could not import google.generativeai. Error: {e}")
        sys.exit(1)

    try:
        import fastapi
        print("SUCCESS: fastapi imported.")
    except ImportError as e:
        print(f"FAILURE: Could not import fastapi. Error: {e}")
        sys.exit(1)

    try:
        import chromadb
        print("SUCCESS: chromadb imported.")
    except ImportError as e:
        print(f"FAILURE: Could not import chromadb. Error: {e}")
        sys.exit(1)

def test_volume_persistence():
    print("\nTesting volume persistence...")
    data_dir = "/home/lancelot/data"
    test_file = os.path.join(data_dir, "test_write.txt")
    
    # Check if directory exists
    if not os.path.exists(data_dir):
        print(f"FAILURE: Data directory {data_dir} does not exist inside the container.")
        # Attempt to create if it doesn't exist, though it should be mapped
        try:
            os.makedirs(data_dir, exist_ok=True)
            print(f"WARNING: Created {data_dir}. Check volume mapping.")
        except PermissionError:
             print(f"FAILURE: Cannot create {data_dir}. Permission denied.")
             sys.exit(1)

    # Try writing
    try:
        with open(test_file, "w") as f:
            f.write("Lancelot was here.")
        print(f"SUCCESS: Wrote to {test_file}.")
    except PermissionError:
        print(f"FAILURE: Permission denied writing to {test_file}. Check user permissions.")
        sys.exit(1)
    except Exception as e:
        print(f"FAILURE: Error writing to file: {e}")
        sys.exit(1)

    # Try reading
    try:
        with open(test_file, "r") as f:
            content = f.read()
            if content == "Lancelot was here.":
                print("SUCCESS: Read correct content from file.")
            else:
                print("FAILURE: Content mismatch.")
                sys.exit(1)
    except Exception as e:
        print(f"FAILURE: Error reading file: {e}")
        sys.exit(1)
        
    print(f"\nPlease verify that 'test_write.txt' exists in your local './lancelot_data' directory.")

def test_user():
    print("\nTesting current user...")
    try:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        print(f"Current user: {user}")
        if user == "root":
            print("WARNING: Container is running as root!")
        else:
            print(f"SUCCESS: Running as non-root user '{user}'.")
    except ImportError:
        # pwd module might not be available on Windows, but this runs in Linux container
        print("Could not determine user via pwd module.")
        print(f"Current UID: {os.getuid()}")

if __name__ == "__main__":
    test_user()
    test_imports()
    test_volume_persistence()
