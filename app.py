import streamlit as st
import pandas as pd
import fitz  # PyMuPDF
import re
import io
import pytesseract
from pdf2image import convert_from_bytes
from datetime import datetime
from PIL import Image

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Extrator PGFN Refinado", page_icon="💎", layout="wide")

# --- FUNÇÕES ---

def parse_currency(value_str):
    if not value_str: return 0.0
    try:
        clean = str(value_str).replace(" ", "").replace("R$", "")
        clean = re.sub(r'[^\d,\.]', '', clean)
        clean = clean.replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

def encontrar_saldo_blindado(text):
    patterns = [
        (r"Saldo\s*Devedor\s*com\s*Juros.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo Devedor c/ Juros"),
        (r"Valor\s*total\s*consolidado.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Vlr Total Consolidado"),
        (r"Total\s*Geral.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total Geral"),
        (r"Total:.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total (Tabela)"),
        (r"(?:Saldo\s*Devedor|Valor\s*Consolidado).*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo/Consolidado Genérico"),
        (r"Total.*?(?:R\$)?.*?([\d\.]+,\d{2})", "Total OCR") 
    ]
    cand = []
    for pat, nome in patterns:
        matches = re.findall(pat, text, re.IGNORECASE | re.DOTALL)
        for m in matches:
            v = parse_currency(m)
            if v > 100: cand.append((v, nome))
    if cand: return max(cand, key=lambda item: item[0])
    return 0.0, "Não encontrado"

def extrair_identificador_inteligente(text):
    """Refina identificador para evitar códigos de barras."""
    
    # 1. Negociação Padrão (7 a 9 dígitos isolados)
    # Evita pegar sequências longas de código de barras
    match_neg = re.findall(r"(?:Negoc|Parcel|Conta).*?(\d{7,9})(?!\d)", text, re.IGNORECASE)
    if match_neg:
        # Pega o primeiro que parecer válido (não é data 2024...)
        for m in match_neg:
            if not m.startswith("202"): return m, "Negociação (7-9 dig)"

    # 2. Inscrição Padrão (11 7 11...) com formatação
    match_insc = re.search(r"(\d{2}\s*\d\s*\d{2}\s*\d{6}[-\s]\d{2})", text)
    if match_insc: return match_insc.group(1).replace("\n", ""), "Inscrição"

    # 3. Fallback: Procura número menor após "Negociação"
    match_curto = re.search(r"Negocia.*?\s(\d{1,8})\b", text, re.IGNORECASE)
    if match_curto: return match_curto.group(1), "Negociação (Curta)"

    return "Desconhecido", "-"

def limpar_modalidade(texto_modalidade):
    """Remove lixo numérico do início da modalidade."""
    if not texto_modalidade: return ""
    # Remove sequências longas de zeros ou números no início que parecem recibo
    # Ex: "00000987246717 COM ATRASO..." -> "COM ATRASO..."
    clean = re.sub(r"^\d{10,}\s*", "", texto_modalidade)
    # Se sobrou "0039 - ...", mantém, pois é código da receita
    return clean.strip()

def inferir_modalidade(text, raw_modalidade=""):
    # Limpeza prévia
    if raw_modalidade:
        raw_modalidade = raw_modalidade.replace("\n", " ").strip()
        raw_modalidade = re.split(r"(?:Data|Situa|Valor|N[º°])", raw_modalidade, flags=re.IGNORECASE)[0]
        raw_modalidade = limpar_modalidade(raw_modalidade)
    
    if len(raw_modalidade) < 5 or "TIPO DE" in raw_modalidade.upper():
        raw_modalidade = ""
    
    if len(raw_modalidade) > 10:
        return raw_modalidade.strip()

    upper = text.upper()
    mapa = {
        "EC 113": "Parcelamento EC 113",
        "EC113": "Parcelamento EC 113",
        "13.485": "PERT (Lei 13.485)",
        "TRANSACAO EXCEPCIONAL": "Transação Excepcional",
        "EXTRAORDINARIA": "Transação Extraordinária",
        "DIVIDA ATIVA": "Dívida Ativa",
        "SIMPLES NACIONAL": "Simples Nacional",
        "SISPAR": "Parcelamento SISPAR",
        "PREVIDENCIARIO": "Previdenciário (Geral)"
    }
    for key, val in mapa.items():
        if key in upper: return val
    return "Não Identificada"

def extrair_modalidade_multilinha(text):
    stop_words = r"(?:Situa|Data|Valor|N[º°]|Inscri|Natureza|Receita|Quant)"
    match_mod = re.search(r"Modalidade[:\s\.]*(.*?)(?=\n\s*" + stop_words + r"|$)", text, re.IGNORECASE | re.DOTALL)
    if match_mod: return match_mod.group(1).strip()
    match_rec = re.search(r"Receita da dívida[:\s\.]*(.*?)(?=\n\s*" + stop_words + r"|$)", text, re.IGNORECASE | re.DOTALL)
    if match_rec: return match_rec.group(1).strip()
    return ""

# --- ENGINE OCR ---
def aplicar_ocr(pdf_bytes):
    try:
        images = convert_from_bytes(pdf_bytes, dpi=300)
        full_text = ""
        for img in images:
            text = pytesseract.image_to_string(img, lang='por')
            full_text += text + "\n"
        return full_text
    except: return ""

def processar(uploaded_file):
    filename = uploaded_file.name
    metodo = "Texto Nativo"
    pdf_bytes = uploaded_file.read()
    
    full_text = ""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc: full_text += page.get_text() + "\n"
    except: pass

    if len(full_text.strip()) < 50:
        metodo = "OCR (Imagem)"
        with st.status(f"Processando {filename}...", expanded=True):
            full_text = aplicar_ocr(pdf_bytes)
    
    identificador, tipo_id = extrair_identificador_inteligente(full_text)
    saldo, metodo_saldo = encontrar_saldo_blindado(full_text)
    
    raw_mod = extrair_modalidade_multilinha(full_text)
    modalidade_final = inferir_modalidade(full_text, raw_mod)
    
    return {
        "Arquivo": filename,
        "Identificador": identificador,
        "Modalidade": modalidade_final,
        "Saldo (R$)": saldo,
        "Método Leitura": metodo
    }

# --- INTERFACE ---
st.title("💎 Extrator PGFN Refinado")
st.markdown("Filtro inteligente de identificadores para evitar números de códigos de barras.")

arquivos = st.file_uploader("Arraste seus PDFs", type=["pdf"], accept_multiple_files=True)

if arquivos:
    if st.button("Processar"):
        dados = []
        bar = st.progress(0)
        
        for i, arq in enumerate(arquivos):
            res = processar(arq)
            dados.append(res)
            bar.progress((i+1)/len(arquivos))
            
        df = pd.DataFrame(dados)
        st.success("Pronto!")
        
        st.dataframe(df.style.format({"Saldo (R$)": "R$ {:,.2f}"}), use_container_width=True)
        st.metric("Total", f"R$ {df['Saldo (R$)'].sum():,.2f}")
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
            ws = writer.sheets['Sheet1']
            ws.set_column('C:C', 50)
            ws.set_column('D:D', 18)
            
        st.download_button("Baixar Excel", buffer.getvalue(), f"PGFN_Refinado_{datetime.now().strftime('%H%M')}.xlsx")
