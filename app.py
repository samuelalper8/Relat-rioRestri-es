import streamlit as st
import fitz  # PyMuPDF
import re
import io
import zipfile
import unicodedata
import json
import urllib.request
from datetime import datetime, date
from difflib import SequenceMatcher

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Relatório de Restrições - ConPrev", layout="wide", page_icon="📋")

# ==============================================================================
# 1. DADOS E CONSTANTES (MUNICÍPIOS)
# ==============================================================================

MUNICIPIOS_POR_UF = {
    "GO": [
        "Amaralina", "Baliza", "Barro Alto", "Bela Vista de Goiás", "Brazabrantes", 
        "Buriti Alegre", "Caiapônia", "Catalão", "Campinaçu", "Ceres", "Córrego do Ouro", 
        "Corumba de Goiás", "Cristalina", "Crixás", "Goiás", "Goiatuba", "Hidrolina", 
        "Itaberaí", "Itapaci", "Jaraguá", "Montes Claros de Goiás", "Nerópolis", 
        "Novo Gama", "Paranaiguara", "Perolândia", "Pilar de Goiás", "Piranhas", 
        "Rianápolis", "Rio Quente", "Serranópolis", "São Francisco de Goiás", 
        "São Luís Montes Belos", "Teresina de Goiás", "Trindade", "Uirapuru"
    ],
    "TO": [
        "Aguiarnópolis", "Almas", "Bandeirantes do Tocantins", "Barra do Ouro", 
        "Brejinho de Nazaré", "Cristalândia", "Goianorte", "Guaraí", "Jaú do Tocantins", 
        "Lajeado", "Maurilândia do Tocantins", "Natividade", "Palmeiras do Tocantins", 
        "Palmeirópolis", "Paraíso do Tocantins", "Paranã", "Pedro Afonso", "Peixe", 
        "Santa Maria do Tocantins", "Santa Rita do Tocantins", "São Valério", "Silvanópolis"
    ],
    "MS": [
        "Alcinópolis", "Anastácio", "Chapadão do Sul", "Coxim", "Iguatemi", 
        "Japorã", "Jaraguari", "Sete Quedas", "Sonora", "Tacuru"
    ],
}

_STOPWORDS_MUN = {"de", "da", "do", "das", "dos", "municipio", "municipio de", "camara", "prefeitura", "municipal"}

# ==============================================================================
# 2. FUNÇÕES UTILITÁRIAS (Adaptadas do seu código)
# ==============================================================================

def normalizar(s: str) -> str:
    t = unicodedata.normalize("NFKD", str(s))
    return t.encode("ascii", "ignore").decode().lower().strip()

def _canon_mun(s: str) -> str:
    if s is None: return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t and t not in _STOPWORDS_MUN]
    return "".join(tokens)

def _tokens_mun(s: str) -> set:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return {t for t in s.split() if t and t not in _STOPWORDS_MUN}

def corresponde_municipio(base_norm: str, mun_norm: str) -> bool:
    if mun_norm == "goias":
        tok_b = _tokens_mun(base_norm)
        if "goias" not in tok_b: return False
        extras = {"go"}
        significativos = {t for t in tok_b if t != "goias" and t not in extras}
        return not significativos

    cb = _canon_mun(base_norm)
    cm = _canon_mun(mun_norm)
    if not cb or not cm: return False
    if cm in cb: return True
    
    tok_m = _tokens_mun(mun_norm)
    tok_b = _tokens_mun(base_norm)
    if tok_m and tok_m.issubset(tok_b): return True
    
    ratio = SequenceMatcher(None, cm, cb).ratio()
    return ratio >= 0.90

def _mask_cnpj_digits(s: str) -> str:
    d = re.sub(r"\D", "", str(s or ""))[:14]
    if len(d) != 14: return s or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"

def _fmt_money(v) -> str:
    if v is None: return "0,00"
    s = str(v).strip()
    if not s: return "0,00"
    if "," in s and any(ch.isdigit() for ch in s): return s
    try:
        num = float(s.replace(".", "").replace(",", "."))
        s = f"{num:,.2f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return s
    except: return s

# --- CND Lookup & Helpers ---
_CNPJ_LOOKUP_CACHE = {}
def _cnpj_lookup_online(cnpj_in: str) -> str:
    try:
        d = re.sub(r"\D", "", str(cnpj_in or ""))[:14]
        if len(d) != 14: return ""
        if d in _CNPJ_LOOKUP_CACHE: return _CNPJ_LOOKUP_CACHE[d]
        
        req = urllib.request.Request(f"https://brasilapi.com.br/api/cnpj/v1/{d}", headers={"User-Agent":"conprev-app"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8","ignore"))
                nome = (data.get("razao_social") or data.get("nome_fantasia") or "").strip()
                if nome:
                    _CNPJ_LOOKUP_CACHE[d] = nome
                    return nome
        return ""
    except: return ""

def _resolve_name_prefer_cnpj(label: str, cnpj_masked: str) -> str:
    nm = _cnpj_lookup_online(cnpj_masked)
    return nm or (label or "")

def _parse_date_br_to_date(s: str):
    if not s: return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(s))
    if not m: return None
    try: return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except: return None

def _cnd_days_color_tuple(days: int):
    if days is None: return (0, 0, 0)
    if days > 90: return (0.05, 0.55, 0.15)
    if days > 30: return (0.95, 0.75, 0.08)
    if days > 0: return (1.00, 0.45, 0.00)
    return (0.90, 0.12, 0.12)

# ==============================================================================
# 3. EXTRAÇÃO DE DADOS (CORE LOGIC)
# ==============================================================================

def _extract_itens_from_stream(file_bytes, filename):
    """Lê bytes do PDF e extrai itens de restrição."""
    itens = []
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        
        # 1. Mapa de CNPJ do cabeçalho
        header_map = {}
        try:
            full_text = ""
            for p in doc: full_text += p.get_text()
            for m in re.finditer(r"CNPJ:\s*([\d\./\-]{14,20}).{0,160}?vinculado.*?\n([^\n]+)", full_text, flags=re.I):
                cn = re.sub(r"\D", "", m.group(1))[:14]
                header_map[cn] = " ".join(m.group(2).split())
        except: pass

        current_cnpj = None
        current_org = None

        for page in doc:
            pf_inside = False
            pf_prev_proc = None
            pf_prev_loc = None
            
            # Extração estruturada (dict)
            blocks = page.get_text("dict")["blocks"]
            lines = []
            for b in blocks:
                for l in b.get("lines", []):
                    text = "".join([s["text"] for s in l.get("spans", [])])
                    text = " ".join(text.split())
                    if text: lines.append(text)
            
            i = 0
            while i < len(lines):
                t = lines[i]
                U = t.upper()

                # A. Cabeçalho CNPJ
                if "CNPJ" in U:
                    m = re.search(r"CNPJ[:\s]*([0-9\.\-\/]{14,18})(?:\s*-\s*(.+))?", t, flags=re.I)
                    if m:
                        current_cnpj = re.sub(r"\D", "", m.group(1))
                        name_inline = (m.group(2) or "").strip()
                        if name_inline and not re.search(r"\d", name_inline):
                            current_org = name_inline
                        else:
                            # Busca nas próximas linhas
                            for k in range(1, 5):
                                if i+k >= len(lines): break
                                nxt = lines[i+k].strip()
                                if len(nxt) >= 5 and not re.search(r"\d", nxt) and "PÁGINA" not in nxt.upper():
                                    current_org = nxt
                                    break
                    i += 1
                    continue
                
                # B. DEVEDOR
                if U == "DEVEDOR":
                    try:
                        # Tenta pegar as linhas anteriores que compõem o registro
                        if i >= 8:
                            cod_nome = lines[i-8]
                            comp = lines[i-7]; venc = lines[i-6]; orig = lines[i-5]
                            dev = lines[i-4]; multa = lines[i-3]; juros = lines[i-2]; cons = lines[i-1]
                            
                            parts = cod_nome.split(" - ", 1)
                            cod = parts[0] if len(parts) > 0 else ""
                            nome = parts[1] if len(parts) > 1 else cod_nome.replace(cod, "").strip()

                            itens.append({
                                "tipo": "DEVEDOR", "cod": cod, "nome": nome, "comp": comp, 
                                "venc": venc, "orig": orig, "dev": dev, "multa": multa, 
                                "juros": juros, "cons": cons,
                                "orgao": _resolve_name_prefer_cnpj(current_org, _mask_cnpj_digits(current_cnpj)),
                                "cnpj": _mask_cnpj_digits(current_cnpj), "src": filename
                            })
                    except:
                        itens.append({"tipo": "DEVEDOR", "raw": t, "src": filename})
                    i += 1
                    continue

                # C. MAED
                if "MAED" in U:
                    try:
                        pa_comp = lines[i+1]; venc = lines[i+2]; orig = lines[i+3]; dev = lines[i+4]; situ = lines[i+5]
                        parts = t.split(" - ", 1)
                        cod = parts[0].strip()
                        desc = parts[1].strip() if len(parts) > 1 else "MAED"
                        
                        comp = pa_comp
                        if re.match(r"\d{2}/\d{2}/\d{4}$", pa_comp):
                            comp = f"{pa_comp[3:5]}/{pa_comp[6:10]}"
                        
                        itens.append({
                            "tipo": "MAED", "cod": cod, "desc": desc, "comp": comp, 
                            "venc": venc, "orig": orig, "dev": dev, "situacao": situ.strip(),
                            "orgao": _resolve_name_prefer_cnpj(current_org, _mask_cnpj_digits(current_cnpj)),
                            "cnpj": _mask_cnpj_digits(current_cnpj), "src": filename
                        })
                    except:
                        itens.append({"tipo": "MAED", "raw": t, "src": filename})
                    i += 1
                    continue

                # D. OMISSÃO
                if "OMISS" in U:
                    periodo = None
                    for k in range(1, 7):
                        if i+k >= len(lines): break
                        look = lines[i+k].upper()
                        if "PERÍODO" in look: continue
                        if re.search(r"\d{4}", look) or re.search(r"\d{2}/\d{4}", look):
                            periodo = lines[i+k]
                            break
                    itens.append({
                        "tipo": "OMISSÃO", "raw": t, "periodo": periodo or "",
                        "orgao": _resolve_name_prefer_cnpj(current_org, _mask_cnpj_digits(current_cnpj)),
                        "cnpj": _mask_cnpj_digits(current_cnpj), "src": filename
                    })
                    i += 1
                    continue
                
                # E. PROCESSO FISCAL (Lógica simplificada para Web)
                if "PROCESSO FISCAL" in U and "PEND" in U:
                    pf_inside = True
                    i += 1; continue
                
                if pf_inside:
                    if "PENDENCIA -" in U: pf_inside = False
                    
                    if "DEVEDOR" in U:
                        # Tenta achar o processo nas linhas vizinhas
                        proc = None
                        for k in range(-5, 5):
                            if i+k >= 0 and i+k < len(lines):
                                m_proc = re.search(r"(\d{4,6}\.\d{3}\.\d{3}/\d{4}-\d{2})", lines[i+k])
                                if m_proc: proc = m_proc.group(1); break
                        
                        if proc:
                            itens.append({
                                "tipo": "PROCESSO FISCAL", "processo": proc, "situacao": "DEVEDOR",
                                "orgao": _resolve_name_prefer_cnpj(current_org, _mask_cnpj_digits(current_cnpj)),
                                "cnpj": _mask_cnpj_digits(current_cnpj), "src": filename
                            })
                
                i += 1

    except Exception as e:
        st.error(f"Erro ao ler PDF {filename}: {e}")
    
    return itens

def _extract_cnd_info_exact_stream(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    texto = ""
    for i, p in enumerate(doc):
        if i > 1: break
        texto += p.get_text()
    
    cnpj = ""; validade = ""; nome = ""
    
    m_nome = re.search(r"(?im)^\s*CNPJ\s*:\s*[0-9\.\-\/]{8,18}\s*[-–—]\s*([^\n]+)$", texto, re.MULTILINE)
    if m_nome and "ENTE FEDERATIVO" not in m_nome.group(1).upper():
        nome = m_nome.group(1).strip()
    elif not nome:
        m_mun = re.search(r"(?im)^\s*Munic[ií]pio\s*:\s*([^\n]+)$", texto, re.MULTILINE)
        if m_mun: nome = m_mun.group(1).strip()

    m_cnpj = re.search(r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", texto)
    if m_cnpj: cnpj = m_cnpj.group(1)

    m_val = re.search(r"(?im)Data\s*de\s*Validade\s*:\s*([0-9]{2}/[0-9]{2}/[0-9]{4})", texto)
    if m_val: validade = m_val.group(1)

    return cnpj, validade, nome

# ==============================================================================
# 4. GERAÇÃO DE RELATÓRIOS PDF (REPORTLAB / FITZ WRAPPER)
# ==============================================================================

def _register_fonts(doc):
    # No Streamlit Cloud, não temos acesso fácil a fontes do Windows.
    # Usaremos fontes padrão do PDF (Helvetica)
    return {"regular": "Helvetica", "bold": "Helvetica-Bold"}

def _draw_header(page, logo_bytes, titulo, info, fonts):
    W, H = page.rect.width, page.rect.height
    margin = 36
    y = margin

    if logo_bytes:
        try:
            rect = fitz.Rect(margin, y, margin+130, y+60)
            page.insert_image(rect, stream=logo_bytes)
        except: pass
    
    text_x = margin + 142
    page.insert_text((text_x, y+16), titulo, fontname=fonts["bold"], fontsize=16)
    page.insert_text((text_x, y+36), info, fontname=fonts["regular"], fontsize=10)
    
    y_sep = y + 76
    page.draw_line((margin, y_sep), (W-margin, y_sep), color=(0,0,0), width=0.7)
    return y_sep + 16, margin

def gerar_pdf_individual(itens, municipio, src_name, logo_bytes):
    doc = fitz.open()
    fonts = _register_fonts(doc)
    A4 = fitz.paper_rect("a4")
    page = doc.new_page(width=A4.height, height=A4.width) # Paisagem se quiser, ou A4 normal
    
    titulo = f"RELATÓRIO DE RESTRIÇÕES · {municipio}"
    info = f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · Fonte: RFB/PGFN"
    
    y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
    
    # Renderização simplificada dos itens
    line_h = 14
    
    def check_page(curr_y):
        if curr_y > A4.width - 40:
            new_p = doc.new_page(width=A4.height, height=A4.width)
            return _draw_header(new_p, logo_bytes, titulo, info, fonts)[0], new_p
        return curr_y, page

    for item in itens:
        tipo = item.get("tipo", "")
        texto = f"[{tipo}] "
        if tipo == "DEVEDOR":
            texto += f"{item.get('cod')} - {item.get('nome')} | Venc: {item.get('venc')} | R$ {item.get('dev')}"
        elif tipo == "MAED":
            texto += f"{item.get('cod')} - {item.get('desc')} | Comp: {item.get('comp')} | R$ {item.get('dev')}"
        elif tipo == "OMISSÃO":
            texto += f"Período: {item.get('periodo')}"
        else:
            texto += str(item.get("raw", ""))[:100]
            
        y, page = check_page(y)
        page.insert_text((x, y), texto, fontname=fonts["regular"], fontsize=10)
        y += line_h

    out_buffer = io.BytesIO()
    doc.save(out_buffer)
    doc.close()
    return out_buffer.getvalue()

def gerar_pdf_gerencial_maed(dados_municipios, logo_bytes):
    doc = fitz.open()
    fonts = _register_fonts(doc)
    page = doc.new_page(width=842, height=595) # A4 Landscape
    titulo = "RELATÓRIO GERENCIAL · MAED"
    info = f"Gerado em {datetime.now().strftime('%d/%m/%Y')}"
    y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
    
    line_h = 16
    
    has_content = False
    for mun, itens in dados_municipios.items():
        maeds = [i for i in itens if i['tipo'] == 'MAED']
        if not maeds: continue
        has_content = True
        
        if y > 550: 
            page = doc.new_page(width=842, height=595)
            y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
            
        page.insert_text((x, y), mun, fontname=fonts["bold"], fontsize=12); y += line_h * 1.5
        
        for d in maeds:
            if y > 550:
                page = doc.new_page(width=842, height=595)
                y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
            
            line = f"• {d.get('cod')} - {d.get('desc')} | Comp: {d.get('comp')} | Venc: {d.get('venc')} | Saldo: R$ {_fmt_money(d.get('dev'))}"
            page.insert_text((x+10, y), line, fontname=fonts["regular"], fontsize=10)
            y += line_h
        y += line_h

    if not has_content:
        page.insert_text((x, y), "Nenhum MAED encontrado nos arquivos selecionados.", fontname=fonts["regular"], fontsize=12)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()

def gerar_pdf_gerencial_devedor(dados_municipios, logo_bytes):
    doc = fitz.open()
    fonts = _register_fonts(doc)
    page = doc.new_page(width=842, height=595)
    titulo = "RELATÓRIO GERENCIAL · DEVEDORES"
    info = f"Gerado em {datetime.now().strftime('%d/%m/%Y')}"
    y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
    line_h = 16
    
    has_content = False
    for mun, itens in dados_municipios.items():
        devs = [i for i in itens if i['tipo'] == 'DEVEDOR']
        # Filtro MAED disfarçado de DEVEDOR
        clean_devs = []
        for d in devs:
            raw = str(d).upper()
            if "MAED" not in raw and "DCTFWEB" not in raw and not str(d.get('cod')).startswith("5440"):
                clean_devs.append(d)
        
        if not clean_devs: continue
        has_content = True

        if y > 550: 
            page = doc.new_page(width=842, height=595)
            y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
            
        page.insert_text((x, y), mun, fontname=fonts["bold"], fontsize=12); y += line_h * 1.5
        
        for d in clean_devs:
            if y > 530: # Item ocupa 2 linhas
                page = doc.new_page(width=842, height=595)
                y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
                
            l1 = f"• {d.get('cod')} - {d.get('nome')} ({d.get('comp')})"
            l2 = f"  Original: R$ {_fmt_money(d.get('orig'))} | Consolidado: R$ {_fmt_money(d.get('cons'))}"
            
            page.insert_text((x+10, y), l1, fontname=fonts["regular"], fontsize=10); y += line_h
            page.insert_text((x+10, y), l2, fontname=fonts["regular"], fontsize=10, color=(0.4, 0.4, 0.4)); y += line_h
        y += line_h

    if not has_content: page.insert_text((x, y), "Nenhum DEVEDOR encontrado.", fontname=fonts["regular"], fontsize=12)
    
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
def gerar_pdf_validade_cnd(lista_cnd, logo_bytes):
    """Gera PDF com lista de CNDs e status colorido (Vencida/A Vencer)."""
    doc = fitz.open()
    fonts = _register_fonts(doc)
    # A4 Retrato é melhor para listas simples
    page = doc.new_page(width=595, height=842) 
    titulo = "RELATÓRIO GERENCIAL · VALIDADE CND"
    info = f"Gerado em {datetime.now().strftime('%d/%m/%Y')} · Fonte: RFB/PGFN"
    
    y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
    line_h = 14
    gap = 24
    
    # Ordena: Vencidas primeiro, depois as mais próximas de vencer
    # (dias ascending: negativos [vencidos] -> pequenos [urgentes] -> grandes [ok])
    lista_cnd.sort(key=lambda k: (k['dias'] is None, k['dias']))

    if not lista_cnd:
        page.insert_text((x, y), "Nenhuma informação de validade encontrada.", fontname=fonts["regular"], fontsize=12)
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    for item in lista_cnd:
        # Pula página se necessário
        if y > 750:
            page = doc.new_page(width=595, height=842)
            y, x = _draw_header(page, logo_bytes, titulo, info, fonts)
        
        # Lógica de Cores
        dias = item['dias']
        color = (0,0,0) # Preto padrão
        msg_dias = "Data inválida"

        if dias is not None:
            if dias < 0:
                color = (0.8, 0.0, 0.0) # Vermelho (Vencida)
                msg_dias = f"VENCIDA há {abs(dias)} dias"
            elif dias == 0:
                color = (0.8, 0.0, 0.0) # Vermelho (Vence hoje)
                msg_dias = "VENCE HOJE"
            elif dias <= 30:
                color = (0.9, 0.5, 0.0) # Laranja (Urgente)
                msg_dias = f"Vence em {dias} dias"
            elif dias <= 90:
                color = (0.8, 0.7, 0.0) # Amarelo (Atenção)
                msg_dias = f"Vence em {dias} dias"
            else:
                color = (0.0, 0.5, 0.0) # Verde (OK)
                msg_dias = f"Vence em {dias} dias"

        # Linha 1: Nome e CNPJ
        nome_display = item['nome'] or "Não identificado"
        cnpj_display = f"(CNPJ: {item['cnpj']})" if item['cnpj'] else ""
        page.insert_text((x, y), f"• {nome_display} {cnpj_display}", fontname=fonts["bold"], fontsize=10)
        y += line_h
        
        # Linha 2: Validade e Status Colorido
        lbl_val = f"  Validade: {item['validade']}  |  Situação: "
        page.insert_text((x, y), lbl_val, fontname=fonts["regular"], fontsize=10)
        
        # Calcula onde desenhar o texto colorido logo após o rótulo
        len_lbl = fitz.get_text_length(lbl_val, fontname=fonts["regular"], fontsize=10)
        page.insert_text((x + len_lbl, y), msg_dias, fontname=fonts["bold"], fontsize=10, color=color)
        
        y += gap

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
# ==============================================================================
# 5. INTERFACE STREAMLIT
# ==============================================================================

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2910/2910768.png", width=60)
    st.title("Configurações")
    
    uploaded_logo = st.file_uploader("Logo para Relatórios (Opcional)", type=["png", "jpg", "jpeg"])
    logo_bytes = uploaded_logo.read() if uploaded_logo else None

    st.markdown("---")
    uf_selecionada = st.selectbox("Selecione a UF", list(MUNICIPIOS_POR_UF.keys()))
    
    todos_municipios = MUNICIPIOS_POR_UF[uf_selecionada]
    
    col_sel1, col_sel2 = st.columns(2)
    if col_sel1.button("Todos"):
        st.session_state[f"mun_{uf_selecionada}"] = todos_municipios
    if col_sel2.button("Limpar"):
        st.session_state[f"mun_{uf_selecionada}"] = []
        
    municipios_selecionados = st.multiselect(
        "Municípios", 
        todos_municipios, 
        key=f"mun_{uf_selecionada}"
    )

st.title("Hub de Relatório de Restrições 🏢")
st.markdown("Faça upload dos PDFs da RFB/PGFN. O sistema identificará automaticamente a qual município pertencem, extrairá DEVEDOR/MAED/OMISSÃO e gerará os relatórios consolidados.")

uploaded_files = st.file_uploader(
    "Carregue os PDFs dos Relatórios de Situação Fiscal", 
    type=["pdf"], 
    accept_multiple_files=True
)

# ... (todo o código anterior permanece igual) ...

if st.button("🚀 Processar Arquivos", type="primary"):
    if not uploaded_files:
        st.warning("Por favor, faça upload de pelo menos um arquivo PDF.")
        st.stop()
    
    if not municipios_selecionados:
        st.warning("Selecione pelo menos um município na barra lateral.")
        st.stop()

    progress_bar = st.progress(0)
    status_text = st.empty()
    
    dados_processados = {m: [] for m in municipios_selecionados}
    fontes_encontradas = {m: None for m in municipios_selecionados}
    
    # --- NOVA LISTA PARA O RELATÓRIO DE CND ---
    lista_cnd_global = [] 

    mapa_norm = {m: normalizar(m) for m in municipios_selecionados}
    total_files = len(uploaded_files)
    arquivos_usados = 0
    
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        
        # A. Análise dos Arquivos
        hoje = date.today() # Data de hoje para cálculo
        
        for idx, file in enumerate(uploaded_files):
            status_text.text(f"Analisando: {file.name}...")
            progress_bar.progress((idx + 1) / total_files)
            
            file_bytes = file.getvalue()
            
            # --- 1. Extração de CND (Para o relatório de validade) ---
            cnpj_cnd, val_cnd, nome_cnd = _extract_cnd_info_exact_stream(file_bytes)
            if val_cnd:
                data_obj = _parse_date_br_to_date(val_cnd)
                dias_restantes = (data_obj - hoje).days if data_obj else None
                
                lista_cnd_global.append({
                    "arquivo": file.name,
                    "nome": nome_cnd,
                    "cnpj": cnpj_cnd,
                    "validade": val_cnd,
                    "dias": dias_restantes
                })

            # --- 2. Identificação do Município (Para os relatórios de restrição) ---
            nome_arquivo = normalizar(file.name)
            municipio_match = None
            for m_real, m_norm in mapa_norm.items():
                if corresponde_municipio(nome_arquivo, m_norm):
                    municipio_match = m_real
                    break
            
            if municipio_match:
                arquivos_usados += 1
                fontes_encontradas[municipio_match] = file.name
                
                itens = _extract_itens_from_stream(file_bytes, file.name)
                dados_processados[municipio_match].extend(itens)
                
                zip_file.writestr(f"Relatorios_Originais/{file.name}", file_bytes)
        
        # B. Geração dos Relatórios Individuais
        status_text.text("Gerando relatórios individuais...")
        for mun, itens in dados_processados.items():
            if itens: 
                pdf_bytes = gerar_pdf_individual(itens, mun, fontes_encontradas[mun], logo_bytes)
                safe_name = mun.replace(" ", "_")
                zip_file.writestr(f"Relatorios_Individuais/{safe_name}_Analise.pdf", pdf_bytes)

        # C. Geração dos Relatórios Gerenciais (Consolidados)
        status_text.text("Gerando relatórios gerenciais...")
        
        pdf_maed = gerar_pdf_gerencial_maed(dados_processados, logo_bytes)
        zip_file.writestr("Relatorios_Gerenciais/MAEDS_Consolidado.pdf", pdf_maed)
        
        pdf_devedor = gerar_pdf_gerencial_devedor(dados_processados, logo_bytes)
        zip_file.writestr("Relatorios_Gerenciais/DEVEDORES_Consolidado.pdf", pdf_devedor)
        
        # D. Validade CND (PDF Colorido) - AQUI ESTAVA O ERRO ANTES
        status_text.text("Gerando relatório de Validade CND...")
        
        # Agora chamamos a função correta que gera PDF, não o TXT
        if lista_cnd_global:
            pdf_cnd = gerar_pdf_validade_cnd(lista_cnd_global, logo_bytes)
            zip_file.writestr("Relatorios_Gerenciais/Validade_CNDs.pdf", pdf_cnd)
        else:
            # PDF vazio avisando que não achou nada
            pdf_cnd = gerar_pdf_validade_cnd([], logo_bytes)
            zip_file.writestr("Relatorios_Gerenciais/Validade_CNDs.pdf", pdf_cnd)

    progress_bar.progress(100)
    status_text.text("Concluído!")
    
    st.success(f"Processamento finalizado! {arquivos_usados} arquivos identificados como municípios cadastrados.")
    
    st.download_button(
        label="📥 Baixar Pacote Completo (.zip)",
        data=zip_buffer.getvalue(),
        file_name=f"Analise_Restricoes_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
        mime="application/zip",
        type="primary"
    )

st.info("Nota: O sistema utiliza algoritmos de reconhecimento de texto para identificar 'DEVEDOR', 'MAED' e 'OMISSÃO'. Verifique sempre os arquivos originais em caso de dúvida.")
