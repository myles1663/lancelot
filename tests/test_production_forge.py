"""
Quest Test Suite - Production Forge
-----------------------------------
Verifies the 3 Core Pillars of the Production Forge Upgrade.

Tests:
1. Antigravity: Browser Launch, Navigation, Visual Receipt.
2. Librarian V2: File Creation, AI Sorting, Trash Rule.
3. Security Bridge: MFA Blocking and Release.
"""

import asyncio
import os
import shutil
import time
from antigravity_engine import AntigravityEngine
from librarian_v2 import LibrarianV2
from security_bridge import MFAListener

DATA_DIR = "./test_data"

async def test_antigravity():
    print("\n--- TEST 1: ANTIGRAVITY ENGINE ---")
    engine = AntigravityEngine(data_dir=DATA_DIR, headless=True)
    try:
        await engine.start()
        print("1.1 Browser Started")
        
        # Test Navigation
        res = await engine.navigate("https://example.com")
        if res.get("status") == "success":
            print(f"1.2 Navigation Success: {res['title']}")
        else:
            print(f"1.2 Navigation Failed: {res.get('error')}")

        # Test Receipt
        receipt = res.get("receipt")
        if receipt and os.path.exists(receipt):
            print(f"1.3 Visual Audit Confirmed: {receipt}")
        else:
            print("1.3 Visual Audit Missing")
            
    except Exception as e:
        print(f"Antigravity Error: {e}")
    finally:
        await engine.stop()

async def test_librarian():
    print("\n--- TEST 2: LIBRARIAN V2 ---")
    
    # Setup directories
    if os.path.exists(DATA_DIR): shutil.rmtree(DATA_DIR)
    os.makedirs(DATA_DIR)
    
    lib = LibrarianV2(data_dir=DATA_DIR)
    lib.start() # Starts observer and consumer task
    print("2.1 Librarian Started")
    
    # Test 1: Financial File
    invoice_path = os.path.join(DATA_DIR, "invoice_123.txt")
    with open(invoice_path, "w") as f:
        f.write("Billing Invoice #123. Total: $500.00 DUE NOW.")
    print(f"2.2 Created {invoice_path}")
    
    # Give it time to process
    await asyncio.sleep(2)
    
    # Check if moved
    fin_dir = os.path.join(DATA_DIR, "Financial")
    trash_dir = os.path.join(DATA_DIR, ".trash")
    
    if os.path.exists(os.path.join(fin_dir, "invoice_123.txt")):
        print("2.3 Sorting Success: Moved to [Financial]")
    else:
        print("2.3 Sorting Failed: File not found in Financial")

    # Test 2: Trash Rule
    # Mock hard delete by moving to trash via API (simulating code calling safe_delete, 
    # though LibrarianV2 mainly organizes. The TrashService is part of it.)
    # Let's test TrashService directly as "Safety Test"
    
    dummy_path = os.path.join(fin_dir, "invoice_123.txt")
    if os.path.exists(dummy_path):
        success = lib.trash_svc.soft_delete(dummy_path, "Test Deletion")
        if success and os.path.exists(os.path.join(trash_dir, f"invoice_123.txt_{int(time.time())}")):
             print("2.4 Safety Success: File Soft Deleted to .trash")
        else:
             print("2.4 Safety Failed: File not verified in .trash")
             
    lib.stop()

async def test_security_bridge():
    print("\n--- TEST 3: SECURITY BRIDGE ---")
    mfa = MFAListener()
    task_id = "test_task_001"
    
    # Start waiter in background
    async def waiter():
        print("3.1 Automation waiting for code...")
        try:
            code = await mfa.wait_for_code(task_id, timeout=2)
            print(f"3.3 Automation received code: {code}")
            return True
        except TimeoutError:
            print("3.3 Automation Timed Out")
            return False

    await mfa.request_mfa(task_id, "Login to Bank")
    
    wait_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.5) # Let it wait
    
    print("3.2 User Submitting Code...")
    mfa.submit_code(task_id, "123456")
    
    result = await wait_task
    if result:
        print("3.4 MFA Handshake Complete")
    else:
        print("3.4 MFA Handshake Failed")

async def main():
    print("STARTING QUEST TESTS...")
    await test_librarian() # Run first to setup dir
    await test_antigravity()
    await test_security_bridge()
    print("\nQUEST COMPLETE.")

if __name__ == "__main__":
    asyncio.run(main())
