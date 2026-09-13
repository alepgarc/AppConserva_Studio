# 🛣️ Rodovia Smart: Sistema de Conservação Rodoviária & IA Multimodal

Aplicação Web completa desenvolvida em **Python** utilizando a biblioteca **Streamlit** (executando via `app.py`) para consolidar evidências de campo de até **80 equipes de conservação rodoviária**, 100% gratuita utilizando **Armazenamento em Base64 no Cloud Firestore (SEM Firebase Storage)** e **Google AI Studio (Gemini Flash)**.

---

## ⚡ Por que esta arquitetura é 100% Gratuita e Sem Cartão de Crédito?

1. **Zero Firebase Storage**: O Firebase Storage exige plano Blaze ou cartão em diversos tipos de contas. **Eliminamos completamente o Storage**.
2. **Armazenamento em Base64**: Cada foto capturada em campo é comprimida dinamicamente no navegador e em Python via Pillow para ~200KB e convertida em string Base64 (< 300KB), armazenada diretamente no documento do Firestore.
3. **Plano Spark Gratuito**: O Firestore oferece **1 GB de armazenamento de dados gratuito**, o que comporta até **~4.000 ativos completos**. O Analista pode baixar o relatório Word (.docx) e o arquivo `.zip` das fotos e limpar o lote com 1 clique quando desejar.
4. **Google AI Studio Gratuito**: O modelo `gemini-2.5-flash` analisa as fotos de campo respeitando a cota gratuita com fila sequencial de 4.5 segundos.

---

## 🎯 Funcionalidades em Destaque

- **🚜 Tela de Campo (Mobile-First)**:
  - Seleção ágil entre 80 Equipes de Conservação Rodoviária.
  - KM obrigatório com gerador dinâmico de identificador de ativo (ex: `PLACA-KM142-01`).
  - Dropdown dos 6 serviços oficiais (Roçada, Troca de Placa, Defensa Metálica, Tachão, Limpeza de Drenagem, Outros).
  - Captura e inserção de coordenadas de GPS.
  - Registro fotográfico direto pela câmera ou upload de arquivo, com compressão adaptativa automática Pillow (~200KB em Base64) e cálculo de economia de dados.
  - Gravação instantânea no banco de dados com feedback visual e notificação de envio.

- **💻 Painel do Analista de Engenharia (Escritório)**:
  - Acesso protegido por senha (`admin123`) com registro de auditoria do engenheiro fiscal.
  - Filtros avançados por Tipo de Serviço, KM, Equipe, Status da IA e Intervalo de Datas.
  - Métricas rápidas em tempo real (Total Filtrados, Pendentes de IA, Analisados, Espaço Spark Utilizado em MB).
  - **🤖 Botão "Rodar Análise da IA em Lote"**: Fila sequencial (respeitando cota de 15 RPM) enviando o Base64 para análise visual do Gemini com diagnósticos técnicos sob normas DNIT e CONTRAN.
  - **Edição & Curadoria**: Visualização de fotografias de campo, link para coordenadas no Google Maps, edição de diagnósticos e recomendações técnicas, e aprovação de ativos.
  - **📄 Gerar Relatório Word (.docx)**: Exportação de documento técnico formal formatado com tabelas de engenharia e dados de fiscalização.
  - **📦 Baixar Fotos (.zip)**: Exportação compilada em arquivo zip com todas as fotografias filtradas renomeadas no padrão `{ID}_{KM}.jpg`.
  - **🗑️ Manutenção de Espaço**: Exclusão segura de lotes com caixa de confirmação para liberação contínua de cota.

- **📊 Indicadores & Mapa**:
  - Gráficos de distribuição de serviços por equipe e elemento rodoviário.
  - Mapeamento georreferenciado dos pontos de fiscalização.

---

## 🚀 Execução

```bash
# Instalação das dependências Python
pip install -r requirements.txt

# Execução da aplicação Streamlit (Porta 3000)
streamlit run app.py --server.port 3000 --server.address 0.0.0.0
```
