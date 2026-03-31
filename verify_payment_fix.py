import json
import sys
import os

# Add the project directory to sys.path so we can import dev_v2
sys.path.append(os.getcwd())

try:
    from dev_v2 import run_agent
except ImportError as e:
    print(f"Error importing run_agent: {e}")
    sys.exit(1)

def test_query(query):
    print(f"\nTesting Query: {query}")
    print("-" * 40)
    try:
        json_report = run_agent(query)
        report = json.loads(json_report)
        answer = report.get("answer", "")
        steps = report.get("steps", [])
        
        print(f"Answer: {answer}")
        print("\nSteps taken:")
        for i, step in enumerate(steps):
            print(f"  {i+1}. {step.get('tool')}")
            
        # Check if it was refused by the old policy
        if "disclose budget or pricing details" in answer and "project handling team" in answer:
            if any(step.get("tool") == "Policy: Pricing/Budget Non-Disclosure" for step in steps):
                print("\nRESULT: FAILED (Refused by old policy gate)")
                return False
        
        print("\nRESULT: PASSED (Proceeded beyond policy gate)")
        return True
    except Exception as e:
        print(f"Error running agent: {e}")
        return False

if __name__ == "__main__":
    queries = [
        "When are invoices raised and what is the payment timeline?",
        "What is the specific price of this project?"
    ]
    
    all_passed = True
    for q in queries:
        if not test_query(q):
            all_passed = False
            
    if all_passed:
        print("\nALL VERIFICATION TESTS PASSED")
        sys.exit(0)
    else:
        print("\nSOME VERIFICATION TESTS FAILED")
        sys.exit(1)
