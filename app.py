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
st.set_page_config(page_title="Extrator OCR PGFN", page_icon="👁️", layout="wide")

# --- FUNÇÕES DE TEXTO E REGEX ---

def parse_currency(value_str):
    if not value_str: return 0.0
    try:
        # Limpa sujeira comum de OCR (ex: trocar 'S' por '5', '.' por ',')
        clean = str(value_str).replace(" ", "").replace("R$", "")
        # Remove caracteres não numéricos exceto vírgula e ponto
        clean = re.sub(r'[^\d,\.]', '', clean)
        # Padroniza para float
        clean = clean.replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

def encontrar_melhor_saldo(text):
    """Lógica 'Blindada' aplicada ao texto (seja ele nativo ou OCR)."""
    # Regex ajustadas para tolerar erros comuns de OCR (espaços extras, troca de letras)
    patterns = [
        (r"Saldo\s*Devedor\s*com\s*Juros.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo Devedor c/ Juros"),
        (r"Valor\s*total\s*consolidado.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Vlr Total Consolidado"),
        (r"Total\s*Geral.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total Geral"),
        (r"Total:.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total (Tabela)"),
        (r"(?:Saldo\s*Devedor|Valor\s*Consolidado).*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo/Consolidado Genérico"),
        # Fallback para OCR sujo que lê 'Total' solto
        (r"\bTotal\b\s*[\.\:_]*\s*(?:R\$)?\s*([\d\.]+,\d{2})", "Total Simples")
    ]

    for pat, nome_metodo in patterns:
        matches = re.findall(pat, text, re.IGNORECASE | re.DOTALL)
        if matches:
            valores = [parse_currency(m) for m in matches]
            valores = [v for v in valores if v > 0]
            if valores:
                return max(valores), nome_metodo

    return 0.0, "Não encontrado"

def extrair_identificador(text):
    # Regex tolerante a OCR
    match_neg = re.search(r"(?:Negociaç[ãa]o|Conta|Parcelamento)[:\s№º\.]*(\d+)", text, re.IGNORECASE)
    if match_neg: return match_neg.group(1), "Negociação"
    
    match_insc = re.search(r"Inscriç[ãa]o[:\s№º\.]*([\d\s\.\/-]+)", text, re.IGNORECASE)
    if match_insc: return match_insc.group(1).strip(), "Inscrição"
    
    return "Desconhecido", "-"

# --- ENGINE OCR ---

def aplicar_ocr(pdf_bytes):
    """Converte PDF em Imagens e aplica OCR (Tesseract)."""
    try:
        # Converte páginas do PDF em imagens (300 DPI é ideal para leitura)
        images = convert_from_bytes(pdf_bytes, dpi=300)
        full_text = ""
        
        for img in images:
            # lang='por' exige o tesseract-ocr-por no packages.txt
            text = pytesseract.image_to_string(img, lang='por')
            full_text += text + "\n"
            
        return full_text
    except Exception as e:
        st.error(f"Erro no OCR: {e}. Verifique se 'poppler-utils' e 'tesseract-ocr' estão instalados.")
        return ""

def processar_hibrido(uploaded_file):
    filename = uploaded_file.name
    metodo_leitura = "Texto Nativo"
    
    # Lê arquivo para bytes
    pdf_bytes = uploaded_file.read()
    
    # 1. Tenta leitura rápida (PyMuPDF)
    full_text = ""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                full_text += page.get_text() + "\n"
    except:
        pass

    # 2. Decisão: Se o texto for muito curto ou vazio, o PDF é uma imagem -> Ativar OCR
    # Limite arbitrário de 50 caracteres para considerar "Vazio/Escaneado"
    if len(full_text.strip()) < 50:
        metodo_leitura = "OCR (Imagem)"
        with st.status(f"Aplicando OCR em {filename}... (Isso é mais lento)", expanded=True):
            full_text = aplicar_ocr(pdf_bytes)
    
    # 3. Extração dos Dados (Mesma lógica blindada)
    identificador, tipo_id = extrair_identificador(full_text)
    saldo, metodo_extracao = encontrar_melhor_saldo(full_text)
    
    # Identificação do Tipo Doc
    tipo_doc = "Genérico"
    upper_text = full_text.upper()
    if "EC 113" in upper_text or "EC113" in upper_text: tipo_doc = "EC 113"
    elif "TRANSAÇÃO" in upper_text: tipo_doc = "Transação"
    elif "REGULARIZE" in upper_text: tipo_doc = "Regularize"

    return {
        "Arquivo": filename,
        "Leitura": metodo_leitura,
        "Tipo Doc": tipo_doc,
        "Identificador": identificador,
        "Saldo (R$)": saldo,
        "Método Extração": metodo_extracao
    }

# --- INTERFACE ---

st.title("👁️ Extrator Híbrido com OCR")
st.markdown("""
Esta versão detecta automaticamente se o PDF é texto ou imagem (escaneado).
* **Texto Nativo:** Processamento instantâneo.
* **Imagem (OCR):** Demora alguns segundos por página para ler o conteúdo.
""")

arquivos = st.file_uploader("Arraste seus PDFs", type=["pdf"], accept_multiple_files=True)

if arquivos:
    if st.button("Iniciar Processamento Inteligente"):
        dados = []
        prog = st.progress(0)
        
        for i, arq in enumerate(arquivos):
            res = processar_hibrido(arq)
            dados.append(res)
            prog.progress((i + 1) / len(arquivos))
        
        df = pd.DataFrame(dados)
        
        st.success("Concluído!")
        
        # Exibe tabela formatada
        st.dataframe(
            df.style.format({"Saldo (R$)": "R$ {:,.2f}"}),
            use_container_width=True
        )
        
        # Métricas
        total = df["Saldo (R$)"].sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("Total", f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c2.metric("Lidos via OCR", len(df[df["Leitura"] == "OCR (Imagem)"]))
        c3.metric("Lidos via Texto", len(df[df["Leitura"] == "Texto Nativo"]))

        # Download
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name="Dados")
            ws = writer.sheets["Dados"]
            wb = writer.book
            fmt = wb.add_format({'num_format': '#,##0.00'})
            ws.set_column('E:E', 18, fmt) # Coluna Saldo
            ws.set_column('A:A', 30)
        
        st.download_button("⬇️ Baixar Excel", buffer.getvalue(), f"OCR_PGFN_{datetime.now().strftime('%H%M')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
