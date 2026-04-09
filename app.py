"""
Contrail Chat — Servidor Flask + Claude API + MySQL
Hospede no Railway ou Render
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import anthropic
import json
import os

app = Flask(__name__)
CORS(app)

# ── Configurações ──────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "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")
DB_HOST     = os.getenv("DB_HOST",     "201.20.10.225")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_NAME     = os.getenv("DB_NAME",     "sim")
DB_USER     = os.getenv("DB_USER",     "acessodb")
DB_PASSWORD = os.getenv("DB_PASSWORD", "fR27dM0l{5{x4(29;'>t")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Conecta ao MySQL ───────────────────────────────
def get_db():
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, connection_timeout=15
    )

# ── Ferramentas disponíveis para o Claude ─────────
def consultar_sql(query: str) -> str:
    """Executa uma query SELECT no banco da Contrail"""
    try:
        # Segurança: só permite SELECT
        q = query.strip().upper()
        if not q.startswith("SELECT"):
            return "Erro: apenas consultas SELECT são permitidas."
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return "Nenhum resultado encontrado."
        # Limita a 100 linhas para não estourar contexto
        if len(rows) > 100:
            rows = rows[:100]
            return json.dumps(rows, ensure_ascii=False, default=str) + "\n(resultado limitado a 100 linhas)"
        return json.dumps(rows, ensure_ascii=False, default=str)
    except Exception as e:
        return f"Erro na consulta: {str(e)}"

def listar_tabelas() -> str:
    """Lista as tabelas disponíveis no banco"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return json.dumps(tables, ensure_ascii=False)
    except Exception as e:
        return f"Erro: {str(e)}"

def descrever_tabela(tabela: str) -> str:
    """Descreve as colunas de uma tabela"""
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"DESCRIBE {tabela}")
        cols = cursor.fetchall()
        conn.close()
        return json.dumps(cols, ensure_ascii=False, default=str)
    except Exception as e:
        return f"Erro: {str(e)}"

# ── Definição das ferramentas para o Claude ────────
TOOLS = [
    {
        "name": "consultar_sql",
        "description": "Executa uma query SELECT no banco de dados da Contrail Logística. Use para responder perguntas sobre viagens, carretas, motoristas, faturamento, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query SQL SELECT válida para executar no banco sim.bi_financeiro"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "listar_tabelas",
        "description": "Lista todas as tabelas disponíveis no banco de dados",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "descrever_tabela",
        "description": "Mostra as colunas e tipos de dados de uma tabela específica",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabela": {
                    "type": "string",
                    "description": "Nome da tabela para descrever"
                }
            },
            "required": ["tabela"]
        }
    }
]

# ── System prompt ──────────────────────────────────
SYSTEM_PROMPT = """Você é o assistente de dados da Contrail Logística S.A., uma empresa de transporte multimodal de Jundiaí-SP.

Você tem acesso ao banco de dados operacional da empresa (MySQL, banco: sim) e pode responder perguntas sobre:
- Viagens e fretes (tabela: bi_financeiro)
- Carretas próprias, agregados e transportadoras
- Faturamento, tarifas e custos
- Motoristas e operações
- Clientes e grupos de clientes

Principais colunas da tabela bi_financeiro:
- carreta: placa da carreta
- transportadora: empresa transportadora (ex: AGREGADOS (CONTRAIL), CONTRAIL DH)
- data_viagem / hora_planejamento: data da viagem
- cliente, grupo_cliente: cliente e grupo
- trajeto, km_trajeto: rota e distância
- operacao: tipo (CABOTAGEM - IMPORTACAO, EXPORTACAO, VAZIO, etc)
- tarifa_total_fornecedor: valor pago ao fornecedor
- especificacao: tipo de serviço

Regras:
- Responda sempre em português brasileiro
- Seja objetivo e direto com os números
- Formate valores monetários como R$ X.XXX
- Ao fazer consultas, sempre filtre por YEAR(hora_planejamento) = 2026 salvo solicitação contrária
- Nunca execute UPDATE, INSERT, DELETE ou DROP
- Se não souber a resposta exata, diga que vai consultar o banco

Quando receber uma pergunta, pense na melhor query SQL para respondê-la e execute."""

# ── Endpoint principal do chat ─────────────────────
@app.route("/chat", methods=["POST"])
def chat():
    data    = request.json
    pergunta = data.get("mensagem", "")
    historico = data.get("historico", [])

    if not pergunta:
        return jsonify({"erro": "Mensagem vazia"}), 400

    # Monta o histórico de mensagens
    messages = historico + [{"role": "user", "content": pergunta}]

    def serializar_content(content):
        """Converte blocos do SDK Anthropic para dicts serializáveis"""
        result = []
        for block in content:
            if hasattr(block, 'type'):
                if block.type == 'text':
                    result.append({"type": "text", "text": block.text})
                elif block.type == 'tool_use':
                    result.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
                else:
                    result.append({"type": block.type})
            elif isinstance(block, dict):
                result.append(block)
        return result

    try:
        # Loop agentic — Claude pode usar ferramentas múltiplas vezes
        while True:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages
            )

            # Serializa o content para poder adicionar ao histórico
            content_serializado = serializar_content(response.content)

            # Adiciona resposta ao histórico
            messages.append({"role": "assistant", "content": content_serializado})

            # Se terminou sem usar ferramenta, retorna
            if response.stop_reason == "end_turn":
                texto = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        texto += block.text
                return jsonify({
                    "resposta": texto,
                    "historico": messages
                })

            # Processa chamadas de ferramentas
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if hasattr(block, 'type') and block.type == "tool_use":
                        nome   = block.name
                        inputs = block.input

                        # Executa a ferramenta
                        if nome == "consultar_sql":
                            resultado = consultar_sql(inputs.get("query", ""))
                        elif nome == "listar_tabelas":
                            resultado = listar_tabelas()
                        elif nome == "descrever_tabela":
                            resultado = descrever_tabela(inputs.get("tabela", ""))
                        else:
                            resultado = "Ferramenta não encontrada"

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": resultado
                        })

                # Adiciona resultados e continua o loop
                messages.append({"role": "user", "content": tool_results})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ── Health check ───────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "app": "Contrail Chat"})

# ── IP externo do servidor ─────────────────────────
@app.route("/ip", methods=["GET"])
def meu_ip():
    try:
        import urllib.request
        ip = urllib.request.urlopen("https://api.ipify.org").read().decode("utf-8")
        return jsonify({"ip_externo": ip})
    except Exception as e:
        return jsonify({"erro": str(e)})

# ── Teste de conexão MySQL ─────────────────────────
@app.route("/ping-db", methods=["GET"])
def ping_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "ok", "mysql": "conectado"})
    except Exception as e:
        return jsonify({"status": "erro", "detalhe": str(e)})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
