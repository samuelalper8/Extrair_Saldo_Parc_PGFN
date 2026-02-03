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
st.set_page_config(page_title="Extrator PGFN Multilinha", page_icon="📜", layout="wide")

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
    """Busca saldo com heurística de OCR e valores máximos."""
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
            if v > 100:
                cand.append((v, nome))
    
    if cand:
        return max(cand, key=lambda item: item[0])
    
    return 0.0, "Não encontrado"

def extrair_identificador_inteligente(text):
    """Tenta achar Negociação/Inscrição mesmo com OCR ruim."""
    match_7 = re.search(r"(?:Negoc|Parcel|Conta).*?(\d{7})(?!\d)", text, re.IGNORECASE)
    if match_7: return match_7.group(1), "Negociação (7 dig)"
    
    match_insc = re.search(r"(\d{2}\s*\d\s*\d{2}\s*\d{6}[-\s]\d{2})", text)
    if match_insc: return match_insc.group(1).replace("\n", ""), "Inscrição"

    match_gen = re.search(r"(?:Negocia|Inscricao).*?[:\.]\s*(\d+)", text, re.IGNORECASE)
    if match_gen: return match_gen.group(1), "Genérico"
    
    return "Desconhecido", "-"

def inferir_modalidade(text, raw_modalidade=""):
    """
    Refina a modalidade capturada ou adivinha pelo contexto se estiver vazia/ruim.
    """
    # Se capturou algo, limpa as quebras de linha para ficar numa linha só
    if raw_modalidade:
        raw_modalidade = raw_modalidade.replace("\n", " ").strip()
        # Remove lixo comum de OCR no final (ex: início do próximo campo)
        raw_modalidade = re.split(r"(?:Data|Situa|Valor|N[º°])", raw_modalidade, flags=re.IGNORECASE)[0]
    
    # Se o texto capturado for inútil ("Tipo de", "Modalidade"), ignora
    if len(raw_modalidade) < 5 or "TIPO DE" in raw_modalidade.upper():
        raw_modalidade = ""
    
    # Se temos um texto decente, retorna ele
    if len(raw_modalidade) > 10:
        return raw_modalidade.strip()

    # Se falhou, tenta inferir pelo texto completo do documento
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
        if key in upper:
            return val
            
    return "Não Identificada"

def extrair_modalidade_multilinha(text):
    """
    Captura o texto da Modalidade/Receita permitindo múltiplas linhas.
    Para apenas quando encontra uma 'Stop Word' (próximo campo).
    """
    # Lista de palavras que indicam o INÍCIO do PRÓXIMO campo
    # Se o regex encontrar isso, ele para de capturar.
    stop_words = r"(?:Situa|Data|Valor|N[º°]|Inscri|Natureza|Receita|Quant)"
    
    # 1. Padrão SISPAR: "Modalidade: ..... (para no próximo campo)"
    # (?s) ativa o DOTALL (ponto pega quebra de linha)
    match_mod = re.search(r"Modalidade[:\s\.]*(.*?)(?=\n\s*" + stop_words + r"|$)", text, re.IGNORECASE | re.DOTALL)
    if match_mod:
        return match_mod.group(1).strip()
    
    # 2. Padrão Regularize: "Receita da dívida: ....."
    match_rec = re.search(r"Receita da dívida[:\s\.]*(.*?)(?=\n\s*" + stop_words + r"|$)", text, re.IGNORECASE | re.DOTALL)
    if match_rec:
        return match_rec.group(1).strip()
    
    # 3. Fallback: "Descrição: ...."
    match_desc = re.search(r"Descriç[:\s\.]*(.*?)(?=\n\s*" + stop_words + r"|$)", text, re.IGNORECASE | re.DOTALL)
    if match_desc:
        return match_desc.group(1).strip()
    
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
    
    # Extração Multilinha
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
st.title("📜 Extrator PGFN Multilinha")
st.markdown("Extração ajustada para textos de Modalidade que quebram linha.")

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
            ws.set_column('C:C', 50) # Coluna Modalidade bem larga
            ws.set_column('D:D', 18)
            
        st.download_button("Baixar Excel", buffer.getvalue(), f"PGFN_Multilinha_{datetime.now().strftime('%H%M')}.xlsx")
