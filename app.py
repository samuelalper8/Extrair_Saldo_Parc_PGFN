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
st.set_page_config(page_title="Extrator PGFN Completo", page_icon="📊", layout="wide")

# --- FUNÇÕES DE TEXTO E REGEX ---

def parse_currency(value_str):
    if not value_str: return 0.0
    try:
        clean = str(value_str).replace(" ", "").replace("R$", "")
        clean = re.sub(r'[^\d,\.]', '', clean)
        clean = clean.replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

def encontrar_melhor_saldo(text):
    """Busca o saldo final com prioridade para Saldo Devedor."""
    patterns = [
        (r"Saldo\s*Devedor\s*com\s*Juros.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo Devedor c/ Juros"),
        (r"Valor\s*total\s*consolidado.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Vlr Total Consolidado"),
        (r"Total\s*Geral.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total Geral"),
        (r"Total:.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total (Tabela)"),
        (r"(?:Saldo\s*Devedor|Valor\s*Consolidado).*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo/Consolidado Genérico"),
        (r"\bTotal\b\s*[\.\:_]*\s*(?:R\$)?\s*([\d\.]+,\d{2})", "Total Simples")
    ]
    for pat, nome_metodo in patterns:
        matches = re.findall(pat, text, re.IGNORECASE | re.DOTALL)
        if matches:
            valores = [parse_currency(m) for m in matches]
            valores = [v for v in valores if v > 0]
            if valores: return max(valores), nome_metodo
    return 0.0, "Não encontrado"

def extrair_identificador_focado(text):
    """Foca no Número da Negociação (7 dígitos)."""
    match_7dig = re.search(r"Negociaç[ãa]o.*?(\d{7})(?!\d)", text, re.IGNORECASE)
    if match_7dig: return match_7dig.group(1), "Negociação (7 dígitos)"

    match_neg_gen = re.search(r"Negociaç[ãa]o[:\s№º\.]*(\d+)", text, re.IGNORECASE)
    if match_neg_gen: return match_neg_gen.group(1), "Negociação (Genérica)"
    
    match_insc = re.search(r"Inscriç[ãa]o[:\s№º\.]*([\d\s\.\/-]+)", text, re.IGNORECASE)
    if match_insc: return match_insc.group(1).strip(), "Inscrição"
    
    return "Desconhecido", "-"

def extrair_modalidade(text):
    """
    Identifica o tipo de dívida ou parcelamento.
    """
    # 1. Tenta capturar o campo explícito "Modalidade:" (Comum no SISPAR)
    # Pega tudo até a quebra de linha
    match_mod = re.search(r"Modalidade[:\s\.]*(.*?)(?:\n|$)", text, re.IGNORECASE)
    if match_mod:
        valor = match_mod.group(1).strip()
        # Filtra se pegou lixo ou texto muito curto
        if len(valor) > 3 and "DATA" not in valor.upper():
            return valor

    # 2. Tenta capturar "Receita da Dívida" (Comum no Regularize)
    match_rec = re.search(r"Receita da dívida[:\s\.]*(.*?)(?:\n|$)", text, re.IGNORECASE)
    if match_rec:
        return match_rec.group(1).strip()

    # 3. Busca por Palavras-Chave (Heurística)
    upper = text.upper()
    
    if "TRANSACAO EXCEPCIONAL" in upper or "TRANSAÇÃO EXCEPCIONAL" in upper:
        return "Transação Excepcional"
    if "EC 113" in upper or "EC113" in upper:
        return "Parcelamento EC 113"
    if "13.485" in upper:
        return "PERT (Lei 13.485)"
    if "SIMPLES NACIONAL" in upper:
        return "Simples Nacional"
    if "DIVIDA ATIVA" in upper or "DÍVIDA ATIVA" in upper:
        return "Dívida Ativa (Geral)"
    if "SISPAR" in upper:
        return "Parcelamento SISPAR"
    
    return "Não Identificada"

# --- ENGINE OCR ---

def aplicar_ocr(pdf_bytes):
    try:
        images = convert_from_bytes(pdf_bytes, dpi=300)
        full_text = ""
        for img in images:
            text = pytesseract.image_to_string(img, lang='por')
            full_text += text + "\n"
        return full_text
    except:
        return ""

def processar_arquivo(uploaded_file):
    filename = uploaded_file.name
    metodo_leitura = "Texto Nativo"
    pdf_bytes = uploaded_file.read()
    
    # 1. Leitura
    full_text = ""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc: full_text += page.get_text() + "\n"
    except: pass

    # 2. OCR se necessário
    if len(full_text.strip()) < 50:
        metodo_leitura = "OCR (Imagem)"
        with st.status(f"Lendo {filename} com OCR...", expanded=True):
            full_text = aplicar_ocr(pdf_bytes)
    
    # 3. Extração
    identificador, tipo_id = extrair_identificador_focado(full_text)
    saldo, metodo_saldo = encontrar_melhor_saldo(full_text)
    modalidade = extrair_modalidade(full_text)
    
    return {
        "Arquivo": filename,
        "Identificador": identificador,
        "Modalidade": modalidade,  # Nova Coluna
        "Saldo (R$)": saldo,
        "Método Leitura": metodo_leitura
    }

# --- INTERFACE ---

st.title("📊 Extrator PGFN (Negociação + Modalidade)")
st.markdown("Extrai **Identificador**, **Modalidade** e **Saldo Final** dos extratos.")

arquivos = st.file_uploader("Arraste seus PDFs", type=["pdf"], accept_multiple_files=True)

if arquivos:
    if st.button("Processar Extratos"):
        dados = []
        bar = st.progress(0)
        
        for i, arq in enumerate(arquivos):
            res = processar_arquivo(arq)
            dados.append(res)
            bar.progress((i + 1) / len(arquivos))
        
        df = pd.DataFrame(dados)
        
        st.success("Processamento Finalizado!")
        
        # Exibição
        st.dataframe(
            df.style.format({"Saldo (R$)": "R$ {:,.2f}"}),
            use_container_width=True
        )
        
        total = df["Saldo (R$)"].sum()
        st.metric("Total Consolidado", f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        # Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name="Extrato")
            ws = writer.sheets["Extrato"]
            fmt = writer.book.add_format({'num_format': '#,##0.00'})
            ws.set_column('D:D', 18, fmt) # Saldo
            ws.set_column('C:C', 35)      # Modalidade (Mais largo)
            ws.set_column('B:B', 20)      # Identificador
            ws.set_column('A:A', 25)      # Arquivo
        
        st.download_button("⬇️ Baixar Excel", buffer.getvalue(), f"Extrato_Modalidades_{datetime.now().strftime('%H%M')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
