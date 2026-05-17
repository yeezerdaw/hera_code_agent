import os

# --- Configuration ---
MARKDOWN_DIR = "markdown_notes"
PLANNER_FILE = "planner_tasks.json"

def setup_directories():
    """Ensures the necessary directories exist."""
    if not os.path.exists(MARKDOWN_DIR):
        os.makedirs(MARKDOWN_DIR)
        print(f"Created directory: {MARKDOWN_DIR}")

def load_planner():
    """Loads planner tasks from a JSON file."""
    if os.path.exists(PLANNER_FILE):
        try:
            with open(PLANNER_FILE, 'r') as f:
                return eval(f.read()) # Using eval for simplicity in this initial version
        except Exception as e:
            print(f"Error loading planner data: {e}")
            return {}
    return {}

def save_planner(planner_data):
    """Saves planner tasks to a JSON file."""
    with open(PLANNER_FILE, 'w') as f:
        f.write(str(planner_data))

def handle_markdown_editor():
    """Handles Markdown file operations."""
    print("\n--- Markdown Editor ---")
    print("Available actions: (1) Create/Edit File, (2) List Files, (3) Back to Menu")
    choice = input("Enter choice: ")

    if choice == '1':
        filename = input(f"Enter filename (e.g., note.md): ")
        if not filename.endswith('.md'):
            filename += ".md"
        filepath = os.path.join(MARKDOWN_DIR, filename)

        print("\n--- Markdown Content ---")
        try:
            with open(filepath, 'r') as f:
                content = f.read()
                print(content)
            
            print("\nType your new content below:")
            new_content = input("New Content: ")
            
            with open(filepath, 'w') as f:
                f.write(new_content)
            print(f"\nSuccessfully saved content to {filepath}")
            
        except FileNotFoundError:
            print(f"Error: File not found at {filepath}. Creating new file.")
            new_content = input("Enter initial content: ")
            with open(filepath, 'w') as f:
                f.write(new_content)
            print(f"Successfully created and saved new file: {filepath}")
        except Exception as e:
            print(f"An error occurred during markdown operation: {e}")

    elif choice == '2':
        print(f"\nFiles in {MARKDOWN_DIR}:")
        files = os.listdir(MARKDOWN_DIR)
        if files:
            for f in files:
                print(f"- {f}")
        else:
            print("No Markdown files found.")
    
    elif choice == '3':
        print("Returning to Main Menu.")
    else:
        print("Invalid choice. Please try again.")


def handle_planner():
    """Handles Planner operations."""
    print("\n--- Planner Manager ---")
    
    planner_data = load_planner()
    
    print("\nAvailable actions: (1) View Tasks, (2) Add Task, (3) Save & Back to Menu")
    choice = input("Enter choice: ")

    if choice == '1':
        if planner_data:
            print("\n--- Your Planner Tasks ---")
            for i, task in enumerate(planner_data.get('tasks', []), 1):
                print(f"{i}. {task}")
        else:
            print("Planner is empty. Add some tasks!")
    
    elif choice == '2':
        task_description = input("Enter the task description: ")
        if task_description:
            # Simple planner: append to a list
            if 'tasks' not in planner_data:
                planner_data['tasks'] = []
            planner_data['tasks'].append(task_description)
            save_planner(planner_data)
            print(f"Task '{task_description}' added successfully.")
        else:
            print("Task description cannot be empty.")

    elif choice == '3':
        save_planner(planner_data)
        print("Planner data saved successfully.")
        
    else:
        print("Invalid choice. Please try again.")


def main_menu():
    """Displays the main menu and handles user flow."""
    setup_directories()
    
    while True:
        print("\n=====================================")
        print("       Markdown & Planner CLI")
        print("=====================================")
        print("1. Markdown Editor")
        print("2. Planner Manager")
        print("3. Exit")
        print("-------------------------------------")
        
        choice = input("Select an option (1-3): ")
        
        if choice == '1':
            handle_markdown_editor()
        elif choice == '2':
            handle_planner()
        elif choice == '3':
            print("Exiting application. Goodbye!")
            break
        else:
            print("Invalid selection. Please choose 1, 2, or 3.")

if __name__ == "__main__":
    main_menu()