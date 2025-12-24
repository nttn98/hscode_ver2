import csv
import json
import re
import os
import requests
from dotenv import load_dotenv
from groq import Groq
from flask import Flask, render_template, request, jsonify
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

app = Flask(__name__)

# --- CẤU HÌNH ---
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_AI = os.getenv("MODEL_AI", "llama-3.3-70b-versatile")
CSV_PATH = os.getenv("CSV_PATH", "data.csv")
JSON_PATH = os.getenv("JSON_PATH", "output.json")

# --- HÀM TIỀN XỬ LÝ TOKEN ---
def get_tokens(text):
    if not text: return set()
    text = str(text).lower()
    # Tách từ, giữ lại các ký tự tiếng Việt có dấu
    tokens = re.findall(r'\b[a-zàáảãạâầấẩẫậăằắẳẵặèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]+\b', text)
    return set(tokens)

def remove_accents(input_str):
    if not input_str: return ""
    s1 = u'ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúýĂăĐđĨĩŨũƠơƯưẠạẢảẤấẦầẨẩẪẫẬậẮắẰằẲẳẴẵẶặẸẹẺẻẼẽẾếỀềỂểỄễỆệỈỉỊịỌọỎỏỐốỒổỔổỖỗỘộỚớỜờỞởỠỡỢợỤụỦủỨứỪừỬửỮữỰựỲỳỶỷỸỹỴỵ'
    s0 = u'AAAAEEEIIOOOOUUYaaaaeeeiiiiiouuyAaDdIiUuOoUuAaAaAaAaAaAaAaAaAaAaAaAaEeEeEeEeEeEeEeEeIiIiOoOoOoOoOoOoOoOoOoOoOoOoUuUuUuUuUuUuUuYyYyYyYy'
    s = ''
    for c in input_str:
        try: s += s0[s1.index(c)]
        except ValueError: s += c
    return s

# --- DATABASE CHƯƠNG ---
def build_chapter_database(csv_file):
    chapters = []
    current_chapter = None
    try:
        with open(csv_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Gom toàn bộ nội dung của các cấp con vào Chương tương ứng
            all_rows = list(reader)
            
            for row in all_rows:
                try: level = int(float(row['level']))
                except: continue
                
                vn = row.get('vn') or ""
                if level == 0:
                    if current_chapter: chapters.append(current_chapter)
                    current_chapter = {
                        "hs_code": str(row.get("hs_code", "")),
                        "vi": vn,
                        "level0_tokens": get_tokens(vn),
                        "all_content_tokens": get_tokens(vn) # Khởi tạo
                    }
                elif current_chapter:
                    # Gộp tokens của các cấp con vào chương hiện tại để phục vụ logic 2
                    current_chapter["all_content_tokens"].update(get_tokens(vn))
                    
            if current_chapter: chapters.append(current_chapter)
    except Exception as e:
        print(f"Lỗi xây dựng DB: {e}")
    return chapters

# --- LOGIC TÌM KIẾM THEO TOKEN (User's Logic) ---
def search_level_0(chapters, query):
    query_tokens = get_tokens(query)
    if not query_tokens: return None
    
    scored_results = []
    for ch in chapters:
        score = 0
        
        # 1. Khớp chính xác trong tiêu đề Level 0 (Ưu tiên cao nhất)
        if query_tokens.issubset(ch["level0_tokens"]):
            score += 10000
            score += (1000 / (len(ch["level0_tokens"]) + 1))
            
        # 2. Khớp trong các cấp con nhưng trả về Level 0 cha
        elif query_tokens.issubset(ch["all_content_tokens"]):
            score += 5000
            score -= (len(ch["all_content_tokens"]) * 0.1)
        
        if score > 0:
            try:
                # Ưu tiên mã HS thấp (nhóm hàng thô ở đầu biểu thuế)
                score += (100 - int(ch["hs_code"][:2]))
            except: pass
            scored_results.append((score, ch))
            
    if not scored_results: return None
    
    scored_results.sort(key=lambda x: x[0], reverse=True)
    return scored_results[0][1]

# --- CÁC HÀM PHỤ TRỢ KHÁC ---
def get_child_from_json(json_file, target_hs_code):
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        node = next((i for i in data if str(i.get('hs_code')) == str(target_hs_code)), None)
        if not node: return []
        flat = []
        def flatten(nodes):
            for n in nodes:
                flat.append({"hs_code": str(n.get('hs_code', '')), "vi": n.get('vi', ''), "en": n.get('en', '')})
                if n.get('children'): flatten(n['children'])
        if node.get('children'): flatten(node['children'])
        return flat
    except: return []

def ask_ai_for_hs_code(query, context):
    ctx = [i for i in context if re.search(r'\d', i.get("hs_code", ""))]
    prompt = f"Sản phẩm: {query}\nContext: {json.dumps(ctx[:150], ensure_ascii=False)}\nChọn mã HS 8 số phù hợp nhất. Trả về JSON: {{\"hs\": \"...\", \"reason\": \"...\"}}"
    try:
        res = client.chat.completions.create(messages=[{"role":"user","content":prompt}], model=MODEL_AI, response_format={"type":"json_object"})
        data = json.loads(res.choices[0].message.content)
        data['hs'] = re.sub(r'\D', '', data.get('hs', ''))
        return data
    except: return {"hs": "N/A", "reason": "Lỗi AI"}

def fetch_caselaw_hierarchy(hs_code):
    if not hs_code or hs_code == "N/A":
        return {"chapter": "", "chapter_groups": {}}

    url = f"https://caselaw.vn/ket-qua-tra-cuu-ma-hs?query={quote_plus(str(hs_code))}"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html_text = resp.text
    except Exception as e:
        print(f"Lỗi Caselaw: {e}")
        return {"chapter": "", "chapter_groups": {}}

    soup = BeautifulSoup(html_text, "html.parser")
    chapters = []
    chapter_groups = {}
    current_ch = ""
    lines = [ln.strip() for ln in soup.get_text(separator="\n").splitlines() if ln.strip()]
    chapter_re = re.compile(r'^(Chương\s+\d+)\s*-?\s*(.*)$', re.I)
    code_re = re.compile(r'^(\d{4,10})\s*-?\s*(.*)$')

    for i, ln in enumerate(lines):
        cm = chapter_re.match(ln)
        if cm:
            title, desc = cm.group(1).strip(), cm.group(2).strip()
            if not desc and i+1 < len(lines):
                desc = lines[i+1].strip()
            chapter_title = f"{title} – {desc}" if desc else title
            chapters.append(chapter_title)
            current_ch = chapter_title
            chapter_groups.setdefault(current_ch, [])
            continue

        cm2 = code_re.match(ln)
        if cm2 and current_ch:
            code, tail = cm2.group(1), cm2.group(2).strip()
            if not tail and i+1 < len(lines):
                tail = lines[i+1].strip()
            chapter_groups[current_ch].append(f"{code} – {tail}" if tail else code)

    return {"chapter": chapters[0] if chapters else "", "chapter_groups": chapter_groups}

# --- ROUTES ---
db = build_chapter_database(CSV_PATH)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search():
    query = request.json.get('query', '').strip()
    if not query: return jsonify({"error": "Vui lòng nhập từ khóa"}), 400
    
    res_ch = search_level_0(db, query)
    if res_ch:
        children = get_child_from_json(JSON_PATH, res_ch['hs_code'])
        kq_ai = ask_ai_for_hs_code(query, children)
        hs_final = kq_ai.get('hs')
        # Tạm thời gọi Caselaw (Bạn hãy copy hàm fetch vào đây)
        caselaw_data = fetch_caselaw_hierarchy(hs_final)
        
        return jsonify({
            "hs_code": hs_final,
            "reason": kq_ai.get('reason'),
            "caselaw": caselaw_data
        })
    return jsonify({"error": "Không tìm thấy"}), 404

if __name__ == "__main__":
    app.run(debug=True)