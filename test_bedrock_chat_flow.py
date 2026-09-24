import json

import requests

BASE = "http://127.0.0.1:8001"


def test_chat():
    print("=== Testing Full AWS Bedrock RAG & Jira Escalation Flow ===")

    # 1. Login
    login_res = requests.post(
        f"{BASE}/login",
        json={"email": "maya.sharma@meridian.com", "password": "password123"},
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    print("[OK] 1. Logged in as Maya Sharma")

    # 2. Create chat session
    sess_res = requests.post(f"{BASE}/sessions", headers=headers)
    assert sess_res.status_code == 200
    sess = sess_res.json()
    sess_id = sess["id"]
    print(f"[OK] 2. Created chat session #{sess_id}")

    # 3. Post Zoom troubleshooting question
    print("[..] 3. Asking AWS Bedrock about Zoom crashing...")
    msg_res = requests.post(
        f"{BASE}/sessions/{sess_id}/messages",
        headers=headers,
        json={
            "message": "My Zoom application keeps freezing and crashing when I open it, how do I fix it?"
        },
    )
    assert msg_res.status_code == 200

    # Check messages
    msgs_res = requests.get(f"{BASE}/sessions/{sess_id}/messages", headers=headers)
    msgs = msgs_res.json()
    assert len(msgs) == 2
    bot_msg = msgs[1]
    assert bot_msg["role"] == "bot"
    assert bot_msg["kind"] == "matches"

    data = json.loads(bot_msg["content"])
    print("\n--- AWS Bedrock Claude 3 Haiku Response (ap-south-1) ---")
    print(data.get("ai_answer"))
    print("---------------------------------------------------------")
    assert "ai_answer" in data and len(data["ai_answer"]) > 50
    assert len(data.get("matches", [])) > 0
    top_match = data["matches"][0]
    print(f"Top Confluence Match: {top_match['title']} (Score: {top_match['score']})")
    assert "Zoom" in top_match["title"]
    print("[OK] 3. AWS Bedrock RAG successfully answered Zoom troubleshooting inquiry!")

    # 4. Test "Not Resolved" -> Jira escalation
    print("\n[..] 4. Testing 'Not Resolved' feedback to create Jira ticket...")
    fb_res = requests.post(
        f"{BASE}/sessions/{sess_id}/feedback",
        headers=headers,
        json={"action": "not_resolved", "message_id": bot_msg["id"]},
    )
    assert fb_res.status_code == 200

    # Check updated messages for jira_ticket
    updated_msgs = requests.get(
        f"{BASE}/sessions/{sess_id}/messages", headers=headers
    ).json()
    jira_msg = next((m for m in updated_msgs if m["kind"] == "jira_ticket"), None)
    assert jira_msg is not None, "Jira ticket message not found"
    jira_data = json.loads(jira_msg["content"])
    print("[OK] 4. Jira Ticket Created Successfully!")
    print(f"     Ticket Key: {jira_data.get('ticket_key')}")
    print(f"     Summary: {jira_data.get('summary')}")
    print(f"     Status: {jira_data.get('status')}")
    print(f"     URL: {jira_data.get('ticket_url')}")

    print("\n" + "=" * 60)
    print(
        "ALL TESTS PASSED: AWS Bedrock ap-south-1 RAG + Jira Escalation 100% OPERATIONAL!"
    )
    print("=" * 60)


if __name__ == "__main__":
    test_chat()
