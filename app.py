import streamlit as st
import pandas as pd
import pdfplumber
import re
import io
import unicodedata
from datetime import datetime

# Configuração da página para o Streamlit Cloud
st.set_page_config(
    page_title="Extrator PGFN Cloud",
    page_icon="📄",
    layout="wide"
)

def normalize_text(text):
    if not text: return ""
    return "".join(
        c for c in unicodedata.normalize('NFKD', text)
        if not unicodedata.combining(c)
    ).upper().strip()

def parse_currency(value_str):
    try:
        # Limpa o formato de moeda brasileiro (Ex: 1.250,50 -> 1250.50)
        clean = str(value_str).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

def extrair_dados(uploaded_files):
    dados_lista = []
    
    for pdf_file in uploaded_files:
        try:
            with pdfplumber.open(pdf_file) as pdf:
                full_text = ""
                for page in pdf.pages:
                    text_page = page.extract_text()
                    if text_page:
                        full_text += text_page + "\n"
                
                # --- Lógica de Extração ---
                
                # 1. Município (Tenta extrair do texto ou usa o nome do ficheiro como fallback)
                muni_match = re.search(r'(?:Contribuinte|Devedor):?\s*([\w\s]+)', full_text, re.IGNORECASE)
                municipio = muni_match.group(1).split('\n')[0].strip() if muni_match else pdf_file.name.split('.')[0]

                # 2. Inscrições (Padrão PGFN: XX X XX XXXXXX-XX)
                inscricoes = re.findall(r'\d{2}\s\d\s\d{2}\s[\d-]+', full_text)
                insc_formatada = ", ".join(set(inscricoes)) if inscricoes else "Verificar PDF"

                # 3. Valor Total
                # Procura por palavras-chave de saldo total seguidas de R$
                valor_total = 0.0
                valor_match = re.search(r'(?:Saldo Devedor Total|Valor Consolidado|Total Atualizado).*?R\$\s*([\d\.,]+)', full_text, re.IGNORECASE | re.DOTALL)
                
                if valor_match:
                    valor_total = parse_currency(valor_match.group(1))
                else:
                    # Fallback: captura todos os valores e assume o último (geralmente o total no rodapé)
                    todos_valores = re.findall(r'R\$\s*([\d\.,]{5,})', full_text)
                    if todos_valores:
                        valor_total = parse_currency(todos_valores[-1])

                dados_lista.append({
                    "Município": municipio.upper(),
                    "Inscrição/Processo": insc_formatada,
                    "Valor (R$)": valor_total,
                    "Ficheiro Original": pdf_file.name
                })
        except Exception as e:
            st.error(f"Erro ao processar {pdf_file.name}: {e}")
            
    return dados_lista

# --- Interface Streamlit ---
st.title("📄 Extrator de PDFs PGFN")
st.info("Ferramenta para extração em lote de valores e processos de extratos PGFN.")

# Upload
arquivos_pdf = st.file_uploader(
    "Carregue aqui os PDFs dos extratos", 
    type=["pdf"], 
    accept_multiple_files=True
)

if arquivos_pdf:
    if st.button("Executar Extração"):
        with st.spinner("A processar ficheiros..."):
            resultados = extrair_dados(arquivos_pdf)
            
            if resultados:
                df = pd.DataFrame(resultados)
                
                # Exibição
                st.subheader("Dados Extraídos")
                st.dataframe(df, use_container_width=True)
                
                # Métrica de Resumo
                total_soma = df["Valor (R$)"].sum()
                st.metric("Soma Total Extraída", f"R$ {total_soma:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

                # Preparar Excel em memória
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='PGFN_Extração')
                
                # Download
                st.download_button(
                    label="⬇️ Descarregar Tabela em Excel",
                    data=output.getvalue(),
                    file_name=f"extracao_pgfn_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("Nenhum dado pôde ser extraído dos ficheiros enviados.")
