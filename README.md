# Ordenador de Orçamentos

Aplicação local para ordenar as linhas de itens de orçamentos VIMAN conforme uma
planilha Excel, sem recriar o restante do PDF. O backend conserva o PDF original e
move apenas recortes vetoriais das linhas de produto para as posições de destino.

## Arquitetura

- **Frontend:** React, TypeScript e Vite.
- **API:** FastAPI, com uploads multipart e respostas tipadas.
- **Excel:** `openpyxl` (`.xlsx`) e `xlrd` (`.xls`), com descoberta da coluna de código.
- **PDF:** PyMuPDF, extração posicional e cópia vetorial de recortes entre páginas.
- **Persistência:** SQLite para metadados/auditoria e diretório privado local para a base atual.

Nenhum documento é enviado a serviços externos. PDFs de trabalho são mantidos em
diretório temporário e removidos automaticamente; apenas a planilha base escolhida
é persistida.

## Executar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

Em outro terminal:

```bash
cd frontend
npm install
npm run dev
```

Acesse `http://localhost:5173`. A documentação da API fica em
`http://localhost:8000/docs`.

## Limites e segurança operacional

O detector é deliberadamente conservador: ele exige cabeçalho de tabela e colunas
de código/descrição reconhecíveis. PDFs digitalizados (sem camada de texto), com
linhas de alturas diferentes ou tabelas fora do padrão devem ser recusados para
revisão, em vez de gerar um documento potencialmente incorreto. Antes de produção,
homologue cada variante de template VIMAN com documentos reais e mantenha backup.

