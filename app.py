import streamlit as st
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from datetime import datetime
import re
import xml.etree.ElementTree as ET
import pdfplumber  # Biblioteca para ler PDF

# --- 1. CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Auditoria INSS - ConPrev", layout="centered", page_icon="🛡️")

# --- 2. SISTEMA DE LOGIN (COM SECRETS RECOMENDADO) ---
def check_password():
    """Retorna True se o usuário tiver a senha correta."""
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    # Campo de senha
    senha_input = st.text_input("Senha de Acesso", type="password")
    
    # Verifica se a senha foi digitada e compara
    # Nota: Em produção, use st.secrets["password"] ao invés de hardcode
    if senha_input:
        if senha_input == "conprev2026": 
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("Senha incorreta")
            
    return False

if not check_password():
    st.stop()

# --- 3. FUNÇÕES DE EXTRAÇÃO ---

def extrair_dados_xml(arquivo):
    """Lê um XML padrão NFS-e e tenta encontrar os dados."""
    try:
        tree = ET.parse(arquivo)
        root = tree.getroot()
        xml_text = ET.tostring(root, encoding='utf8', method='xml').decode()
        
        # Regex para capturar dados
        cnpj_match = re.search(r'<Cnpj>(.*?)</Cnpj>', xml_text, re.IGNORECASE)
        valor_match = re.search(r'<ValorServicos>(.*?)</ValorServicos>', xml_text, re.IGNORECASE)
        inss_match = re.search(r'<ValorInss>(.*?)</ValorInss>', xml_text, re.IGNORECASE)
        prestador_match = re.search(r'<RazaoSocial>(.*?)</RazaoSocial>', xml_text, re.IGNORECASE)
        numero_match = re.search(r'<Numero>(.*?)</Numero>', xml_text, re.IGNORECASE)

        dados = {}
        if cnpj_match: dados['cnpj'] = cnpj_match.group(1)
        if valor_match: dados['valor'] = float(valor_match.group(1))
        if inss_match: dados['inss'] = float(inss_match.group(1))
        if prestador_match: dados['prestador'] = prestador_match.group(1)
        if numero_match: dados['numero'] = numero_match.group(1)
        
        return dados
    except Exception as e:
        st.error(f"Erro ao ler XML: {e}")
        return None

def extrair_dados_pdf(arquivo):
    """Lê um PDF de texto e tenta encontrar padrões (Regex)."""
    try:
        text = ""
        with pdfplumber.open(arquivo) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        
        dados = {}
        
        # Padrões comuns de Regex
        cnpj_match = re.search(r'CNPJ:?\s?(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})', text)
        if cnpj_match: dados['cnpj'] = cnpj_match.group(1)
        
        valor_match = re.search(r'TOTAL DO SERVIÇO:?\s?R?\$?\s?([\d.,]+)', text, re.IGNORECASE)
        if not valor_match:
            valor_match = re.search(r'VALOR TOTAL:?\s?R?\$?\s?([\d.,]+)', text, re.IGNORECASE)
        
        if valor_match:
            valor_str = valor_match.group(1).replace('.', '').replace(',', '.')
            dados['valor'] = float(valor_str)
            
        inss_match = re.search(r'INSS RETIDO:?\s?R?\$?\s?([\d.,]+)', text, re.IGNORECASE)
        if inss_match:
            inss_str = inss_match.group(1).replace('.', '').replace(',', '.')
            dados['inss'] = float(inss_str)

        num_match = re.search(r'N[ºo].?\s?(\d+)', text, re.IGNORECASE)
        if num_match: dados['numero'] = num_match.group(1)

        return dados
    except Exception as e:
        st.error(f"Erro ao ler PDF: {e}")
        return None

# --- 4. GERENCIAMENTO DE ESTADO ---
if 'form_prestador' not in st.session_state: st.session_state['form_prestador'] = ''
if 'form_cnpj' not in st.session_state: st.session_state['form_cnpj'] = ''
if 'form_numero' not in st.session_state: st.session_state['form_numero'] = ''
if 'form_valor' not in st.session_state: st.session_state['form_valor'] = 0.00
if 'form_retencao' not in st.session_state: st.session_state['form_retencao'] = 0.00

# --- 5. BARRA LATERAL (UPLOAD) ---
with st.sidebar:
    st.header("📂 Automação")
    st.info("Faça upload da Nota Fiscal (XML ou PDF) para preencher os campos automaticamente.")
    uploaded_file = st.file_uploader("Carregar Nota Fiscal", type=['xml', 'pdf'])
    
    if uploaded_file is not None:
        if st.button("🚀 Processar Arquivo"):
            dados_extraidos = {}
            if uploaded_file.type == "text/xml":
                dados_extraidos = extrair_dados_xml(uploaded_file)
            elif uploaded_file.type == "application/pdf":
                dados_extraidos = extrair_dados_pdf(uploaded_file)
            
            if dados_extraidos:
                if 'prestador' in dados_extraidos: st.session_state['form_prestador'] = dados_extraidos['prestador']
                if 'cnpj' in dados_extraidos: st.session_state['form_cnpj'] = dados_extraidos['cnpj']
                if 'numero' in dados_extraidos: st.session_state['form_numero'] = dados_extraidos['numero']
                if 'valor' in dados_extraidos: st.session_state['form_valor'] = dados_extraidos['valor']
                if 'inss' in dados_extraidos: st.session_state['form_retencao'] = dados_extraidos['inss']
                st.success("Dados carregados com sucesso!")
                st.rerun()
            else:
                st.warning("Não foi possível extrair dados automaticamente deste arquivo.")

# --- 6. FUNÇÕES UTILITÁRIAS ---
def formatar_cnpj(cnpj):
    if not cnpj: return ""
    cnpj = re.sub(r'\D', '', str(cnpj))
    if len(cnpj) == 14:
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    return cnpj

def gerar_pdf(dados):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Cores
    cor_primaria = HexColor("#0E2F5A")    # Azul ConPrev
    cor_secundaria = HexColor("#F0F2F6") 
    cor_borda = HexColor("#D1D5DB")
    
    # Cabeçalho
    c.setFillColor(cor_primaria)
    c.rect(0, height - 100, width, 100, fill=True, stroke=False)
    
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(40, height - 50, "RELATÓRIO DE AUDITORIA")
    c.setFont("Helvetica", 12)
    c.drawString(40, height - 70, "Retenção de INSS - Lei 9.711/98")
    
    data_hora = datetime.now().strftime('%d/%m/%Y')
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - 40, height - 50, f"DATA: {data_hora}")
    
    # Dados Prestador
    y = height - 140
    c.setFillColor(cor_secundaria)
    c.roundRect(40, y - 70, width - 80, 80, 10, fill=True, stroke=False)
    
    c.setFillColor(cor_primaria)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y - 15, "1. DADOS DA NOTA FISCAL")
    
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y - 45, "PRESTADOR:")
    c.drawString(350, y - 45, "CNPJ:")
    c.setFont("Helvetica", 10)
    c.drawString(130, y - 45, str(dados['prestador'])[:35])
    c.drawString(400, y - 45, dados['cnpj'])
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y - 60, "NÚMERO NF:")
    c.drawString(350, y - 60, "VALOR:")
    c.setFont("Helvetica", 10)
    c.drawString(130, y - 60, str(dados['num_nf']))
    c.drawString(400, y - 60, f"R$ {dados['valor_servico']:,.2f}")

    # Análise
    y -= 110
    c.setFillColor(cor_primaria)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "2. ANÁLISE TRIBUTÁRIA")
    y -= 30
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.black)
    c.drawString(40, y, f"Simples Nacional? {dados['is_simples'].upper()}  |  Anexo IV? {dados['is_anexo_iv'].upper()}")
    
    # Resultado Box
    y -= 70
    cor_res = HexColor("#E8F5E9") if dados['status'] == "OK" else HexColor("#FFEBEE")
    c.setFillColor(cor_res)
    c.roundRect(40, y - 80, width - 80, 90, 6, fill=True, stroke=False)
    
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(55, y - 15, "3. APURAÇÃO E RESULTADO")
    
    c.setFont("Helvetica", 10)
    c.drawString(55, y - 40, f"INSS Devido: R$ {dados['retencao_devida']:,.2f}")
    c.drawString(55, y - 55, f"INSS na Nota: R$ {dados['retencao_destacada']:,.2f}")
    
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(width - 60, y - 40, f"Diferença: R$ {dados['diferenca']:,.2f}")
    
    if dados['status'] == "OK":
        c.setFillColor(HexColor("#2E7D32"))
        msg = "✅ APROVADO"
    else:
        c.setFillColor(HexColor("#C62828"))
        msg = "❌ DIVERGENTE"
    c.drawRightString(width - 60, y - 60, msg)

    # Rodapé
    c.setStrokeColor(cor_borda)
    c.line(40, 50, width - 40, 50)
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.gray)
    c.drawString(40, 35, "ConPrev Assessoria - Auditoria Fiscal Automatizada")
    
    c.save()
    buffer.seek(0)
    return buffer

# --- 7. INTERFACE PRINCIPAL ---

st.title("🛡️ Auditoria de Retenção de INSS")
st.markdown("---")

st.header("1. Dados da Nota Fiscal")
col1, col2 = st.columns(2)

with col1:
    prestador = st.text_input("Nome do Prestador", key='form_prestador')
    cnpj_input = st.text_input("CNPJ", key='form_cnpj', max_chars=18)
    cnpj = formatar_cnpj(cnpj_input)
    if cnpj_input and len(cnpj) < 18:
        st.caption(f"Detectado: {cnpj}")

with col2:
    num_nf = st.text_input("Número da NF", key='form_numero')
    valor_servico = st.number_input("Valor Total do Serviço (R$)", min_value=0.0, step=0.01, format="%.2f", key='form_valor')

# --- FASE 2: TRIAGEM ---
st.header("2. Triagem Tributária")
col_t1, col_t2 = st.columns(2)
with col_t1:
    is_simples = st.radio("Simples Nacional?", ["Não", "Sim"], horizontal=True)
with col_t2:
    is_anexo_iv = "Não"
    if is_simples == "Sim":
        is_anexo_iv = st.radio("Atividade Anexo IV?", ["Não", "Sim"], horizontal=True)

if is_simples == "Sim" and is_anexo_iv == "Não":
    st.success("✅ **Sem Retenção.** Prestador Simples Nacional (Anexos I, II, III ou V).")
else:
    # --- FASE 3: CÁLCULOS ---
    st.header("3. Conferência")
    
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        percentual_base = st.slider("Base de Cálculo Legal (%)", 0, 100, 100)
    with col_c2:
        mao_obra_nfs = st.number_input("Mão de Obra na Nota (R$)", min_value=0.0, step=0.01, format="%.2f")

    base_final = max(valor_servico * (percentual_base / 100), mao_obra_nfs)
    retencao_devida = base_final * 0.11
    
    st.info(f"Base de Cálculo: **R$ {base_final:,.2f}** | Retenção Esperada (11%): **R$ {retencao_devida:,.2f}**")
    
    # Campo de Retenção Destacada
    retencao_destacada = st.number_input("INSS Destacado na Nota (R$)", min_value=0.0, step=0.01, format="%.2f", key='form_retencao')
    
    diferenca = retencao_devida - retencao_destacada
    
    st.markdown("### Resultado")
    status_audit = "OK"
    orientacao = "Valores conferem. Arquivar."
    
    if abs(diferenca) < 0.05:
        st.success(f"✅ **APROVADO!** Valor destacado: R$ {retencao_destacada:,.2f}")
    else:
        status_audit = "DIVERGENTE"
        st.error(f"❌ **DIVERGÊNCIA DE R$ {diferenca:,.2f}**")
        if retencao_destacada < retencao_devida:
            orientacao = "Reter a diferença no pagamento ou pedir carta de correção."
            st.warning("⚠️ O valor na nota é MENOR que o devido. O cliente corre risco fiscal.")
        else:
            orientacao = "Valor na nota é maior. Verificar base de cálculo."
            st.warning("⚠️ O valor na nota é MAIOR que o devido.")

    # --- BOTÃO PDF ---
    if prestador:
        dados_pdf = {
            'prestador': prestador, 'cnpj': cnpj, 'num_nf': num_nf,
            'valor_servico': valor_servico, 'is_simples': is_simples, 'is_anexo_iv': is_anexo_iv,
            'percentual_base': percentual_base, 'base_final': base_final,
            'retencao_devida': retencao_devida, 'retencao_destacada': retencao_destacada,
            'diferenca': diferenca, 'status': status_audit, 'orientacao': orientacao
        }
        pdf = gerar_pdf(dados_pdf)
        st.download_button("⬇️ Baixar Relatório PDF", pdf, f"Auditoria_{prestador}.pdf", "application/pdf")