"""
Unified Meridian OfficeTech backend: auth + per-user chat sessions/messages in the DB
+ knowledge base search (same FAISS logic as api_server.py).

This REPLACES user_api.py and api_server.py for the Meridian OfficeTech app.
(Your separate IT self-help chatbot can keep using api_server.py as-is.)

Install:
    pip install fastapi uvicorn sqlalchemy "passlib[bcrypt]" python-jose sentence-transformers faiss-cpu numpy

Run:
    uvicorn backend_main:app --reload --port 8001
"""

import json
import os
import pickle
import re
import shutil
from datetime import datetime

import boto3
import faiss
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session


def generate_bedrock_answer(user_query: str, matches: list) -> str:
    """
    Generate an enterprise-grade AI answer using AWS Bedrock (Claude 3 Haiku)
    in region ap-south-1 (Mumbai) grounded strictly on retrieved Confluence documentation.
    """
    try:
        aws_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION", "ap-south-1")
        if not aws_key or not aws_secret:
            return None

        br = boto3.client(
            "bedrock-runtime",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region,
        )

        context_blocks = []
        for i, m in enumerate(matches[:3], 1):
            title = m.get("title", f"Document {i}")
            text = m.get("text", "")
            context_blocks.append(f"[{i}] {title}\n{text}")
        context_str = "\n\n".join(context_blocks)

        system_prompt = (
            "You are the Meridian Enterprise AI Copilot, running on AWS Bedrock in ap-south-1. "
            "Your role is to assist employees with technical troubleshooting, invoicing workflows, tax codes, and internal SOPs. "
            "Answer the employee inquiry accurately, professionally, and clearly using the verified Confluence knowledge base context provided below. "
            "Provide structured, numbered step-by-step guidance where applicable. "
            "If the documentation mentions escalation to L2 Support or specific rules (such as GST 28% remarks or OS privacy settings), highlight them clearly. "
            "Do not fabricate facts outside the documentation."
        )

        user_content = f"Verified Confluence Documentation Context:\n{context_str}\n\nEmployee Inquiry: {user_query}\n\nPlease provide a clear, structured response:"

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 600,
            "temperature": 0.2,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }

        res = br.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0", body=json.dumps(payload)
        )
        body = json.loads(res["body"].read().decode("utf-8"))
        answer = body["content"][0]["text"].strip()
        return answer
    except Exception as e:
        print(f"[AWS Bedrock ap-south-1 Error]: {e}")
        return None


def is_conversational_greeting(query: str) -> bool:
    q = query.strip().lower()
    cleaned = re.sub(r"[^a-zA-Z0-9 ]", "", q)
    greetings = [
        "hello",
        "hi",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "who are you",
        "what can you do",
        "help me",
        "how are you",
        "namaste",
        "greetings",
    ]
    if any(cleaned == g or cleaned.startswith(g + " ") for g in greetings):
        return True
    if "my name is" in cleaned or "i am " in cleaned:
        words = cleaned.split()
        if len(words) <= 7 and not any(
            w in words for w in ["invoice", "zoom", "camera", "tax", "gst"]
        ):
            return True
    return False


def generate_bedrock_conversational_reply(user_query: str) -> str:
    """Generate a warm, professional greeting and capability overview via Bedrock Claude."""
    try:
        aws_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION", "ap-south-1")
        if not aws_key or not aws_secret:
            return "Hello! I am your Meridian Enterprise Copilot. How can I help you today?"

        br = boto3.client(
            "bedrock-runtime",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region,
        )
        prompt = (
            f"The employee said: '{user_query}'. "
            "Respond politely and warmly. Introduce yourself as the Meridian Enterprise AI Copilot powered by AWS Bedrock in ap-south-1. "
            "Briefly mention what you can help with: Zoom troubleshooting, Windows camera & microphone privacy permissions, invoicing & GST tax code guidelines, and Jira L2 Support escalation. "
            "Keep the response welcoming, concise, and professional."
        )
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 250,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }
        res = br.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0", body=json.dumps(payload)
        )
        body = json.loads(res["body"].read().decode("utf-8"))
        return body["content"][0]["text"].strip()
    except Exception as e:
        print(f"[Bedrock Greeting Error]: {e}")
        return "Hello! I am your Meridian Enterprise Copilot powered by AWS Bedrock. How can I assist you today?"


from .auth_dependency import get_current_user, get_db, require_employee
from .auth_utils import create_access_token, hash_password, verify_password
from .database import (
    ChatMessage,
    ChatSession,
    CompanyProject,
    EmployeeEmail,
    EmployeeMeeting,
    Invoice,
    JiraTicket,
    LeaveRequest,
    Product,
    User,
    init_db,
)
from .jira_service import create_jira_issue

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploaded_tax_documents")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Real-world-style GST tax codes. "GST 28% (New Category)" is the newly
# onboarded bracket - mirrors the "new tax code" scenario from the POC,
# and is the one that requires Remarks (see the invoice validation below).
VALID_TAX_CODES = ["GST 5%", "GST 12%", "GST 18%", "GST 28% (New Category)"]
NEW_TAX_CODE = "GST 28% (New Category)"

# ------------------ Knowledge base search setup ------------------
INDEX_FILE = os.path.join(BASE_DIR, "knowledge.index")
META_FILE = os.path.join(BASE_DIR, "knowledge_meta.json")
BM25_FILE = os.path.join(BASE_DIR, "bm25_index.pkl")
MODEL_NAME = "all-MiniLM-L6-v2"

# RRF fusion constant - 60 is the standard default from the original RRF paper,
# rarely needs tuning. Confidence threshold is on a DIFFERENT scale than
# before (fused RRF scores, normalized to ~0-1) - see note below.
RRF_K = 60
CONFIDENCE_THRESHOLD = 0.30
TOP_K = 3
CANDIDATE_K = 20  # how many candidates each method contributes before fusion

print("Loading embedding model, FAISS index, and BM25 index...")
model = SentenceTransformer(MODEL_NAME)
index = faiss.read_index(INDEX_FILE)
with open(META_FILE, "r", encoding="utf-8") as f:
    meta = json.load(f)
with open(BM25_FILE, "rb") as f:
    bm25 = pickle.load(f)["bm25"]
print(f"Ready. FAISS has {index.ntotal} vectors, BM25 covers {len(meta)} chunks.")


def tokenize(text):
    return re.findall(r"\w+", text.lower())


def dense_search(query, top_k=CANDIDATE_K):
    """Semantic search via FAISS - returns [(chunk_index, cosine_score), ...]."""
    query_vec = model.encode([query], convert_to_numpy=True)
    faiss.normalize_L2(query_vec)
    scores, indices = index.search(query_vec, top_k)
    return [
        (int(idx), float(score))
        for idx, score in zip(indices[0], scores[0])
        if idx != -1
    ]


def lexical_search(query, top_k=CANDIDATE_K):
    """Keyword search via BM25 - returns [(chunk_index, bm25_score), ...]."""
    scores = bm25.get_scores(tokenize(query))
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[
        :top_k
    ]
    return [(i, float(scores[i])) for i in top_indices]


def cloud_bedrock_retrieve(query, top_k=TOP_K):
    """
    Retrieve vector embeddings directly from the AWS Bedrock Cloud Vector Store (Amazon OpenSearch Serverless).
    """
    kb_id = os.getenv("AWS_BEDROCK_KB_ID")
    if not kb_id:
        return None
    try:
        aws_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        kb_region = os.getenv("AWS_BEDROCK_KB_REGION") or os.getenv(
            "AWS_REGION", "ap-south-1"
        )
        rt = boto3.client(
            "bedrock-agent-runtime",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=kb_region,
        )
        res = rt.retrieve(knowledgeBaseId=kb_id, retrievalQuery={"text": query})
        items = []
        for r in res.get("retrievalResults", []):
            score = r.get("score", 0.75)
            text = r.get("content", {}).get("text", "")
            s3_uri = r.get("location", {}).get("s3Location", {}).get("uri", "")

            # Map filename back to Confluence title and URL if available
            fname = os.path.basename(s3_uri)
            clean_fname = (
                fname.lower().replace(".md", "").replace("_", "").replace("-", "")
            )
            matched_meta = next(
                (
                    m
                    for m in meta
                    if clean_fname
                    in m["title"]
                    .lower()
                    .replace(" ", "")
                    .replace("_", "")
                    .replace("-", "")
                ),
                None,
            )
            title = (
                matched_meta["title"]
                if matched_meta
                else fname.replace(".md", "").replace("_", " ")
            )
            url = matched_meta["url"] if matched_meta else s3_uri

            items.append(
                {"score": round(score, 4), "title": title, "url": url, "text": text}
            )
        if items and items[0]["score"] >= CONFIDENCE_THRESHOLD:
            return items
    except Exception as e:
        print(f"[AWS Cloud Vector Store Warning]: {e}")
    return None


def search(query, top_k=TOP_K):
    """
    Primary: Queries the AWS Bedrock Cloud Vector Store (Amazon OpenSearch Serverless).
    Fallback: Hybrid dense FAISS + BM25 local index.
    """
    cloud_results = cloud_bedrock_retrieve(query, top_k)
    if cloud_results:
        return cloud_results

    dense_items = dense_search(query)
    lexical_items = lexical_search(query)

    dense_map = {idx: score for idx, score in dense_items}
    lexical_map = {idx: score for idx, score in lexical_items}

    # Normalize BM25 scores relative to max BM25 score in the lexical results
    max_lex = (
        max(lexical_map.values())
        if lexical_map and max(lexical_map.values()) > 0
        else 1.0
    )

    all_indices = set(dense_map.keys()) | set(lexical_map.keys())
    scored_chunks = []

    for idx in all_indices:
        # Cosine score ranges -1 to 1; clamp negative scores to 0
        c_score = max(0.0, dense_map.get(idx, 0.0))
        l_score = max(0.0, lexical_map.get(idx, 0.0)) / max_lex

        # 75% semantic similarity + 25% normalized keyword overlap
        hybrid_score = min(0.75 * c_score + 0.25 * l_score, 1.0)
        scored_chunks.append((idx, hybrid_score))

    ranked = sorted(scored_chunks, key=lambda x: x[1], reverse=True)[:top_k]

    results = []
    for idx, score in ranked:
        chunk = meta[idx]
        results.append(
            {
                "score": round(score, 4),
                "title": chunk["title"],
                "url": chunk["url"],
                "text": chunk["text"],
            }
        )
    return results


# ------------------ App setup ------------------
init_db()
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ Static Page Routes ------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
@app.get("/portal")
@app.get("/employee_portal.html")
def serve_portal():
    fpath = os.path.join(BASE_DIR, "employee_portal.html")
    if os.path.exists(fpath):
        return FileResponse(fpath, media_type="text/html")
    raise HTTPException(status_code=404, detail="employee_portal.html not found")


@app.get("/login")
@app.get("/login.html")
def serve_login():
    fpath = os.path.join(BASE_DIR, "login.html")
    if os.path.exists(fpath):
        return FileResponse(fpath, media_type="text/html")
    raise HTTPException(status_code=404, detail="login.html not found")


@app.get("/customer_store.html")
def serve_customer_store():
    fpath = os.path.join(BASE_DIR, "customer_store.html")
    if os.path.exists(fpath):
        return FileResponse(fpath, media_type="text/html")
    raise HTTPException(status_code=404, detail="customer_store.html not found")


# ------------------ Schemas ------------------
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    role: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str = "customer"


class UserProfileOut(BaseModel):
    id: int
    email: str
    name: str = "Maya Sharma"
    role: str

    class Config:
        from_attributes = True


class CustomerProductOut(BaseModel):
    id: int
    name: str
    price: float
    rating: float
    review_count: int
    category: str
    discount_percent: int
    image_url: str | None
    delivery_estimate: str
    original_price: float

    class Config:
        from_attributes = True


class MeetingOut(BaseModel):
    id: int
    title: str
    date_time: str
    attendees: str
    link: str
    status: str
    tags: str

    class Config:
        from_attributes = True


class EmailOut(BaseModel):
    id: int
    sender: str
    sender_email: str
    subject: str
    preview: str
    timestamp: str
    is_read: bool
    is_urgent: bool

    class Config:
        from_attributes = True


class EmailToggleReadOut(BaseModel):
    id: int
    is_read: bool


class LeaveCreateIn(BaseModel):
    leave_type: str
    start_date: str
    end_date: str
    days_count: int = 1
    reason: str


class LeaveOut(BaseModel):
    id: int
    user_id: int
    leave_type: str
    start_date: str
    end_date: str
    days_count: int
    reason: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class LeaveDashboardOut(BaseModel):
    balances: dict[str, int]
    requests: list[LeaveOut]


class CompanyProjectOut(BaseModel):
    id: int
    title: str
    client: str
    status: str
    progress_pct: int
    budget_allocated: float
    budget_spent: float
    deadline: str
    finance_lead: str

    class Config:
        from_attributes = True


class SessionOut(BaseModel):
    id: int
    title: str

    class Config:
        from_attributes = True


class MessageIn(BaseModel):
    message: str


class MessageOut(BaseModel):
    id: int | None = None
    session_id: int | None = None
    role: str
    kind: str
    content: str  # plain text, or JSON string when kind == "matches"

    class Config:
        from_attributes = True


class FeedbackIn(BaseModel):
    action: str  # "resolved" or "not_resolved"
    message_id: int | None = None


class ChatReplyOut(BaseModel):
    escalate: bool
    top_score: float
    matches: list


class ProductOut(BaseModel):
    id: int
    name: str
    price: float
    show_in_invoice_dropdown: int

    class Config:
        from_attributes = True


class ProductCreate(BaseModel):
    name: str
    price: float
    show_in_invoice_dropdown: bool = True


class InvoiceCreate(BaseModel):
    client_name: str
    product_id: int
    quantity: int = 1
    tax_code: str = "GST 18%"
    tax_document_filename: str | None = None
    remarks: str | None = None
    status: str | None = None


class InvoiceOut(BaseModel):
    id: int
    client_name: str
    product_id: int
    quantity: int
    tax_code: str
    tax_document_filename: str | None
    remarks: str | None
    status: str

    class Config:
        from_attributes = True


class InvoiceStatusUpdate(BaseModel):
    status: str | None = None
    client_name: str | None = None
    product_id: int | None = None
    quantity: int | None = None
    tax_code: str | None = None
    remarks: str | None = None


class TaxDocumentOut(BaseModel):
    filename: str


# ------------------ Auth endpoints ------------------
@app.post("/signup", response_model=TokenResponse)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(
            status_code=400, detail="An account with this email already exists"
        )
    desired_role = req.role if req.role in ("employee", "customer") else "customer"
    user = User(
        email=req.email, hashed_password=hash_password(req.password), role=desired_role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        role=user.role or "customer",
    )


@app.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email_query = req.email.strip().lower()
    if email_query in ("maya", "maya.sharma", "maya_sharma", "maya@meridian.com"):
        email_query = "maya.sharma@meridian.com"
    elif email_query in ("customer", "cust"):
        email_query = "customer@meridian.com"
    user = db.query(User).filter(User.email.ilike(email_query)).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        role=user.role or "customer",
    )


@app.get("/me", response_model=UserProfileOut)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


def make_chat_title(user_query: str, match_title: str | None = None) -> str:
    """
    Generate short, ChatGPT-style conversation titles (2 to 4 words, max 26 chars)
    e.g. 'Tax Code Guide', 'Raise Invoice', 'Leave Application', 'Teams Login Fix'.
    """
    q_lower = (user_query or "").lower().strip()

    # 1. High priority domain topics
    if "gst 28" in q_lower or "28%" in q_lower or "inv-rem-102" in q_lower:
        return "GST 28% Remarks"
    if "tax code" in q_lower:
        return "Tax Codes & Invoicing"
    if "leave" in q_lower or "wfh" in q_lower or "vacation" in q_lower:
        return "Leave & WFH Policy"
    if "raise" in q_lower and "invoice" in q_lower:
        return "Raise New Invoice"
    if "invoice" in q_lower:
        return "Invoice Inquiries"
    if "teams" in q_lower or "login" in q_lower:
        return "Teams Login Help"
    if "european" in q_lower or "aggregation" in q_lower or "vat" in q_lower:
        return "European Tax Review"
    if "product" in q_lower and (
        "add" in q_lower or "catalog" in q_lower or "master" in q_lower
    ):
        return "Add Catalog Product"
    if "jira" in q_lower or "escalat" in q_lower:
        return "Support Escalation"

    # 2. Clean from document match title if available
    if match_title:
        clean = match_title
        for prefix in [
            "Understanding ",
            "How to ",
            "Guide to ",
            "Fixing ",
            "Overview of ",
            "Instructions for ",
            "Process for ",
        ]:
            if clean.lower().startswith(prefix.lower()):
                clean = clean[len(prefix) :]
        for suffix in [
            " in the invoicing system",
            " in the product catalog",
            " in meridian",
            " procedure",
            " guidelines",
        ]:
            if clean.lower().endswith(suffix.lower()):
                clean = clean[: -len(suffix)]

        words = clean.strip().split()
        if len(words) <= 4:
            res = " ".join(words).title()
            if len(res) <= 26:
                return res

    # 3. Fallback: extract key words from user query
    stopwords = {
        "what",
        "is",
        "the",
        "rule",
        "for",
        "how",
        "do",
        "i",
        "can",
        "you",
        "tell",
        "me",
        "about",
        "please",
        "help",
        "with",
        "a",
        "an",
        "and",
        "or",
        "to",
        "in",
        "of",
        "on",
        "at",
    }
    cleaned_words = [
        w.strip("?,.!") for w in q_lower.split() if w.strip("?,.!") not in stopwords
    ]
    if cleaned_words:
        selected = cleaned_words[:3]
        title = " ".join(selected).title()
        if len(title) > 26:
            title = title[:24].rstrip() + "…"
        return title

    return "New Conversation"


# ------------------ Chat session endpoints ------------------
@app.post("/sessions", response_model=SessionOut)
def create_session(
    current_user: User = Depends(require_employee), db: Session = Depends(get_db)
):
    session = ChatSession(user_id=current_user.id, title="New conversation")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@app.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    current_user: User = Depends(require_employee), db: Session = Depends(get_db)
):
    return (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.created_at.desc())
        .all()
    )


@app.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.query(ChatMessage).filter(ChatMessage.session_id == session.id).delete()
    db.delete(session)
    db.commit()
    return {"status": "ok"}


@app.patch("/sessions/{session_id}")
def rename_session(
    session_id: int,
    req: dict,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    new_title = req.get("title", "").strip()
    if new_title:
        session.title = new_title[:60]
        db.commit()
    return {"status": "ok", "title": session.title}


@app.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def get_messages(
    session_id: int,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.messages


@app.post("/sessions/{session_id}/messages", response_model=ChatReplyOut)
def post_message(
    session_id: int,
    req: MessageIn,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save the user's message
    user_msg = ChatMessage(
        session_id=session.id, role="user", kind="text", content=req.message
    )
    db.add(user_msg)

    # Intercept conversational greetings / introductions
    if is_conversational_greeting(req.message):
        greeting_reply = generate_bedrock_conversational_reply(req.message)
        if session.title in ("New conversation", "New Conversation"):
            session.title = "Welcome & Greetings"
        bot_msg = ChatMessage(
            session_id=session.id, role="bot", kind="text", content=greeting_reply
        )
        db.add(bot_msg)
        db.commit()
        return ChatReplyOut(escalate=False, top_score=1.0, matches=[])

    # Run the actual search
    results = search(req.message)
    top_score = results[0]["score"] if results else 0.0
    escalate = (not results) or (top_score < CONFIDENCE_THRESHOLD)

    # Title the session with clean, short ChatGPT-style topic name
    if session.title in ("New conversation", "New Conversation"):
        match_title = results[0]["title"] if (not escalate and results) else None
        session.title = make_chat_title(req.message, match_title)

    # Save the bot's response
    if escalate:
        bot_msg = ChatMessage(
            session_id=session.id,
            role="bot",
            kind="escalate",
            content="I could not find specific documentation matching your query in the Meridian Confluence knowledge base. Would you like to create an L2 IT Support ticket?",
        )
    else:
        # Generate intelligent AI response via AWS Bedrock (Claude 3 Haiku in ap-south-1)
        ai_answer = generate_bedrock_answer(req.message, results)
        payload = {"ai_answer": ai_answer, "matches": results}
        bot_msg = ChatMessage(
            session_id=session.id,
            role="bot",
            kind="matches",
            content=json.dumps(payload),
        )
    db.add(bot_msg)

    db.commit()

    return ChatReplyOut(escalate=escalate, top_score=top_score, matches=results)


@app.post("/sessions/{session_id}/feedback")
def record_feedback(
    session_id: int,
    feedback: FeedbackIn,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if feedback.action == "not_resolved":
        # Extract user query and history
        user_query = "Unresolved Assistance Request"
        history = []
        for m in session.messages:
            history.append({"role": m.role, "kind": m.kind, "content": m.content})
            if m.role == "user" and m.content:
                user_query = m.content

        # Generate Jira issue (live Atlassian API or enterprise simulated mode)
        jira_res = create_jira_issue(
            session_id=session.id,
            user_email=current_user.email,
            user_query=user_query,
            chat_history=history,
        )

        # Save to database
        db_ticket = JiraTicket(
            ticket_key=jira_res["ticket_key"],
            session_id=session.id,
            reporter_email=current_user.email,
            summary=jira_res["summary"],
            user_query=jira_res["user_query"],
            transcript=jira_res["transcript"],
            status=jira_res["status"],
            priority=jira_res["priority"],
            ticket_url=jira_res["ticket_url"],
            is_live=jira_res["is_live_jira"],
        )
        db.add(db_ticket)

        # Append Jira ticket message to conversation thread
        bot_msg = ChatMessage(
            session_id=session.id,
            role="bot",
            kind="jira_ticket",
            content=json.dumps(jira_res),
        )
        db.add(bot_msg)
        db.commit()
        return {"status": "ok", "action": feedback.action, "jira_ticket": jira_res}

    elif feedback.action == "resolved":
        bot_msg = ChatMessage(
            session_id=session.id,
            role="bot",
            kind="resolved",
            content=json.dumps(
                {
                    "status": "Resolved",
                    "note": "Employee confirmed query was successfully resolved.",
                }
            ),
        )
        db.add(bot_msg)
        db.commit()
        return {"status": "ok", "action": feedback.action}

    return {"status": "ok", "action": feedback.action}


@app.get("/health")
def health():
    return {"status": "ok", "vectors_indexed": index.ntotal}


# ------------------ Product endpoints ------------------
@app.get("/products", response_model=list[ProductOut])
def list_products(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return db.query(Product).all()


@app.post("/products", response_model=ProductOut)
def create_product(
    req: ProductCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # NOTE: no admin-role check yet - fine for a single-user POC, but before
    # multiple users exist this should be restricted to an admin role.
    product = Product(
        name=req.name,
        price=req.price,
        show_in_invoice_dropdown=1 if req.show_in_invoice_dropdown else 0,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


# ------------------ Tax document upload (Employee only) ------------------
@app.post("/tax-documents", response_model=TaxDocumentOut)
def upload_tax_document(
    client_name: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(require_employee),
):
    # Filename is namespaced by client, since requirement 3 is: the tax
    # document that applies depends on WHICH client the invoice is for -
    # re-uploading for a different client shouldn't overwrite another's file.
    safe_client = re.sub(r"[^\w\-]", "_", client_name)
    saved_name = f"{safe_client}__{file.filename}"
    dest_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return TaxDocumentOut(filename=saved_name)


# ------------------ Invoice endpoints (Employee only) ------------------
@app.post("/invoices", response_model=InvoiceOut)
def create_invoice(
    req: InvoiceCreate,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    product = db.query(Product).filter(Product.id == req.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if req.tax_code not in VALID_TAX_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tax code. Must be one of: {', '.join(VALID_TAX_CODES)}",
        )

    # Real backend enforcement of the "remarks required for the new tax
    # bracket" rule - the frontend's disabled-textarea bug fails to satisfy
    # this, giving you a genuine end-to-end bug to debug.
    if req.tax_code == NEW_TAX_CODE and not (req.remarks and req.remarks.strip()):
        raise HTTPException(
            status_code=400,
            detail=f"Remarks is required for tax code {NEW_TAX_CODE}. (Error code: INV-REM-102)",
        )

    invoice = Invoice(
        user_id=current_user.id,
        client_name=req.client_name,
        product_id=req.product_id,
        quantity=req.quantity,
        tax_code=req.tax_code,
        tax_document_filename=req.tax_document_filename,
        remarks=req.remarks,
        status=req.status if req.status else "Raised",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


@app.get("/invoices", response_model=list[InvoiceOut])
def list_invoices(
    current_user: User = Depends(require_employee), db: Session = Depends(get_db)
):
    return db.query(Invoice).filter(Invoice.user_id == current_user.id).all()


@app.patch("/invoices/{invoice_id}", response_model=InvoiceOut)
def update_invoice_status(
    invoice_id: int,
    req: InvoiceStatusUpdate,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id, Invoice.user_id == current_user.id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if req.status is not None:
        invoice.status = req.status
    if req.client_name is not None:
        invoice.client_name = req.client_name
    if req.product_id is not None:
        invoice.product_id = req.product_id
    if req.quantity is not None:
        invoice.quantity = req.quantity
    if req.tax_code is not None:
        invoice.tax_code = req.tax_code
    if req.remarks is not None:
        invoice.remarks = req.remarks
    db.commit()
    db.refresh(invoice)
    return invoice


# ------------------ Employee Workspace Endpoints ------------------
@app.get("/employee/meetings", response_model=list[MeetingOut])
def get_employee_meetings(
    current_user: User = Depends(require_employee), db: Session = Depends(get_db)
):
    return db.query(EmployeeMeeting).order_by(EmployeeMeeting.id.asc()).all()


@app.get("/employee/emails", response_model=list[EmailOut])
def get_employee_emails(
    current_user: User = Depends(require_employee), db: Session = Depends(get_db)
):
    return db.query(EmployeeEmail).order_by(EmployeeEmail.id.asc()).all()


@app.patch("/employee/emails/{email_id}/toggle-read", response_model=EmailToggleReadOut)
def toggle_email_read(
    email_id: int,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    email = db.query(EmployeeEmail).filter(EmployeeEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    email.is_read = not bool(email.is_read)
    db.commit()
    db.refresh(email)
    return EmailToggleReadOut(id=email.id, is_read=email.is_read)


@app.get("/employee/leaves", response_model=LeaveDashboardOut)
def get_employee_leaves(
    current_user: User = Depends(require_employee), db: Session = Depends(get_db)
):
    user_leaves = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.user_id == current_user.id)
        .order_by(LeaveRequest.created_at.desc())
        .all()
    )

    # Starting annual leave allowances
    quotas = {
        "Earned Leave": 14,
        "Sick Leave": 8,
        "Casual Leave": 6,
        "Work From Home": 8,
    }
    for req in user_leaves:
        if req.status in ("Approved", "Pending") and req.leave_type in quotas:
            quotas[req.leave_type] = max(
                0, quotas[req.leave_type] - (req.days_count or 1)
            )

    return LeaveDashboardOut(balances=quotas, requests=user_leaves)


@app.post("/employee/leaves", response_model=LeaveOut)
def apply_leave(
    req: LeaveCreateIn,
    current_user: User = Depends(require_employee),
    db: Session = Depends(get_db),
):
    leave = LeaveRequest(
        user_id=current_user.id,
        leave_type=req.leave_type,
        start_date=req.start_date,
        end_date=req.end_date,
        days_count=max(1, req.days_count),
        reason=req.reason,
        status="Pending",
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


@app.get("/employee/projects", response_model=list[CompanyProjectOut])
def get_company_projects(
    current_user: User = Depends(require_employee), db: Session = Depends(get_db)
):
    return db.query(CompanyProject).order_by(CompanyProject.id.asc()).all()


# ------------------ Customer Storefront Endpoints ------------------
@app.get("/api/customer/products", response_model=list[CustomerProductOut])
def get_customer_products(
    category: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Product)
    if category and category.strip().lower() not in ("all", "all categories"):
        query = query.filter(Product.category.ilike(f"%{category.strip()}%"))
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(Product.name.ilike(term) | Product.category.ilike(term))
    products = query.all()
    results = []
    for p in products:
        disc = p.discount_percent if p.discount_percent is not None else 15
        orig_price = (
            round(p.price / (1.0 - (disc / 100.0)), 2)
            if disc < 100
            else round(p.price * 1.25, 2)
        )
        results.append(
            {
                "id": p.id,
                "name": p.name,
                "price": p.price,
                "rating": p.rating if p.rating is not None else 4.5,
                "review_count": p.review_count if p.review_count is not None else 120,
                "category": p.category or "Office Tech",
                "discount_percent": disc,
                "image_url": p.image_url,
                "delivery_estimate": p.delivery_estimate or "Tomorrow, 11 AM",
                "original_price": orig_price,
            }
        )
    return results


# ------------------ Static Frontend Serving & No-Cache ------------------
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path.lower()
    if (
        path == "/"
        or path.endswith(".html")
        or path.startswith("/employee")
        or path.startswith("/api")
    ):
        response.headers["Cache-Control"] = (
            "no-cache, no-store, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
def serve_root():
    store_path = os.path.join(BASE_DIR, "customer_store.html")
    if os.path.exists(store_path):
        return FileResponse(store_path)
    return FileResponse(os.path.join(BASE_DIR, "login.html"))


@app.get("/meridian.html")
@app.get("/shopmart.html")
@app.get("/shopmart_app.html")
def redirect_legacy():
    content = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
<script>
  const r = localStorage.getItem("user_role");
  if (r === "employee") { window.location.replace("employee_portal.html"); }
  else { window.location.replace("customer_store.html"); }
</script>
</head><body>Redirecting to portal...</body></html>"""
    return HTMLResponse(
        content, headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")
