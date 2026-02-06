import streamlit as st
import pandas as pd
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
import re
import io
import os

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Classificador Fiscal (Nome + Conteúdo)", page_icon="🧠", layout="wide")

st.title("🧠 Classificador Inteligente (Metadados + OCR)")
st.markdown("Esta versão usa o **Nome do Arquivo** para preencher lacunas que o OCR não consegue ler.")

# --- CONFIGURAÇÃO DE AMBIENTE ---
POPPLER_PATH = None
if os.name == 'nt': 
    POPPLER_PATH = r'C:\poppler-24.02.0\Library\bin' # Ajuste se rodar localmente

# --- FUNÇÃO DE CLASSIFICAÇÃO AVANÇADA ---
def classificar_por_nome(nome_arquivo, classificacao_atual):
    """Refina a classificação baseada no nome do arquivo se o OCR falhou ou foi genérico."""
    nome = nome_arquivo.upper()
    
    # Se já foi identificado como PASEP pelo código 3703, mantém.
    if classificacao_atual == "PASEP":
        return classificacao_atual

    # Regras baseadas nos nomes dos seus arquivos
    if "PASEP" in nome:
        return "PASEP"
    if "13.485" in nome:
        return "Previdenciário Especial (Lei 13.485)"
    if "12.810" in nome:
        return "Previdenciário (Lei 12.810)"
    if "EC_113" in nome or "EC 113" in nome:
        return "Previdenciário (EC 113)"
    if "PGFN" in nome:
        return "PGFN (Dívida Ativa)"
    if "RPPS" in nome:
        return "Previdenciário (RPPS)"
    
    return classificacao_atual

# --- FUNÇÃO DE EXTRAÇÃO ---
def extrair_dados_turbo(file_bytes, file_name):
    texto_completo = ""
    metodo = "Digital"
    
    # 1. Leitura Digital
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages[:4]:
                texto = page.extract_text()
                if texto:
                    texto_completo += texto + "\n"
    except:
        pass

    # 2. OCR (Se necessário)
    if len(texto_completo.strip()) < 50:
        metodo = "OCR"
        try:
            images = convert_from_bytes(file_bytes, first_page=1, last_page=3, poppler_path=POPPLER_PATH)
            for img in images:
                texto_completo += pytesseract.image_to_string(img, lang='por') + "\n"
        except:
            pass # Segue com o que tem

    # --- 3. EXTRAÇÃO DE DADOS ---
    
    # Processo
    match_proc = re.search(r'(?:Parcelamento|Processo|N[ºo°] do Parcelamento)[:\s\.]+([\d\.\/-]+)', texto_completo, re.IGNORECASE)
    processo = match_proc.group(1).strip() if match_proc else "Não identificado"
    
    # Modalidade (Pelo texto)
    modalidade = "Outros"
    if "Simplificado" in texto_completo or "OPP" in texto_completo:
        modalidade = "Simplificado (OPP)"
    elif "13.485" in texto_completo or "13485" in file_name:
        modalidade = "Lei 13.485/17"
    elif "SIPADE" in texto_completo:
        modalidade = "Ordinário/Especial"
    
    # Saldo Devedor
    saldo = 0.0
    padrao_valor = r'R\$\s?([\d\.\s]+,\d{2})'
    match_saldo = re.search(r'(?:Saldo devedor|Dívida consolidada|Valor Consolidado|Total).*?' + padrao_valor, texto_completo, re.IGNORECASE | re.DOTALL)
    
    if match_saldo:
        valor_str = match_saldo.group(1).replace('.', '').replace(' ', '').replace(',', '.')
        try:
            saldo = float(valor_str)
        except:
            saldo = 0.0

    # --- 4. CLASSIFICAÇÃO (HÍBRIDA) ---
    classificacao = "A Verificar"
    texto_limpo = texto_completo.replace('O', '0').upper()
    
    # Regras de Texto (Prioridade Máxima)
    if "3703" in texto_limpo:
        classificacao = "PASEP"
    elif any(cod in texto_limpo for cod in ["1082", "1138", "1646", "CPSS"]):
        classificacao = "Previdenciário"
    elif "PREVIDENCIARIO" in texto_limpo:
        classificacao = "Previdenciário"
        
    # Regra de Refinamento pelo Nome do Arquivo (A Mágica acontece aqui)
    classificacao = classificar_por_nome(file_name, classificacao)

    return {
        "Nome Arquivo": file_name,
        "Processo": processo,
        "Modalidade": modalidade,
        "Classificação": classificacao,
        "Saldo Devedor (R$)": saldo
    }

# --- INTERFACE ---
uploaded_files = st.file_uploader("Arraste seus PDFs", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Processar Inteligente"):
        dados = []
        bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            file_bytes = file.getvalue()
            info = extrair_dados_turbo(file_bytes, file.name)
            dados.append(info)
            bar.progress((i + 1) / len(uploaded_files))
        
        df = pd.DataFrame(dados)
        
        # Totais
        total = df["Saldo Devedor (R$)"].sum()
        col1, col2 = st.columns(2)
        col1.metric("Total Identificado", f"R$ {total:,.2f}")
        col2.metric("Arquivos", len(df))
        
        # Tabela
        st.dataframe(df.style.format({"Saldo Devedor (R$)": "R$ {:,.2f}"}), use_container_width=True)
        
        # Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Baixar Excel Melhorado", buffer.getvalue(), "Relatorio_Inteligente.xlsx")
