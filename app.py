"""
Contrail Chat - Servidor Flask + Claude API + API TI Contrail
Otimizado para Deploy no Railway com correção de CORS
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import requests
import json
import os

app = Flask(__name__)

# Configuração de CORS: Permite apenas seu domínio do GitHub e localhost para segurança
CORS(app, resources={
    r"/*": {
        "origins": ["https://gabrisou.github.io", "http://localhost:5000"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-API-Key", "Authorization"]
    }
})

# Configurações via Variáveis de Ambiente (Recomendado para Railway)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CONTRAIL_API_BASE = os.getenv("CONTRAIL_API_BASE", "https://api-read.contrail.com.br")
CONTRAIL_API_KEY  = os.getenv("CONTRAIL_API_KEY", "3Ydk7CP3JRJMOH9zU1qKSk3VT5k0bRMmh77FaeZsVPdOCXff")
HEADERS_TI        = {"X-API-Key": CONTRAIL_API_KEY}
LIMIT_MAX         = 500

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# --- Funções de Apoio ---
def consultar_endpoint(endpoint, params):
    try:
        if "limit" in params:
            params["limit"] = min(int(params["limit"]), LIMIT_MAX)
        r = requests.get(
            f"{CONTRAIL_API_BASE}/api/v1/{endpoint}",
            headers=HEADERS_TI,
            params=params,
            timeout=30
        )
        return r.json()
    except Exception as e:
        return {"erro": str(e)}

def meta_endpoint(endpoint):
    try:
        r = requests.get(
            f"{CONTRAIL_API_BASE}/api/v1/meta/{endpoint}",
            headers=HEADERS_TI,
            timeout=15
        )
        return r.json()
    except Exception as e:
        return {"erro": str(e)}

def chamar_ferramenta(nome, inputs):
    mapas = {
        "consultar_financeiro": "financeiro",
        "consultar_tracking": "tracking",
        "consultar_documentos": "documentos",
        "consultar_movimentos": "movimentos-cheio",
        "meta_financeiro": "financeiro",
        "meta_tracking": "tracking",
        "meta_documentos": "documentos",
        "meta_movimentos": "movimentos-cheio"
    }
    
    if nome in mapas:
        if "meta_" in nome:
            return json.dumps(meta_endpoint(mapas[nome]), ensure_ascii=False, default=str)
        return json.dumps(consultar_endpoint(mapas[nome], inputs), ensure_ascii=False, default=str)
    return "Ferramenta não encontrada"

# --- Definição das Ferramentas (Tools) ---
TOOLS = [
    {
        "name": "consultar_financeiro",
        "description": "Consulta dados de viagens, fretes, carretas e faturamento (bi_financeiro).",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"}, "order_by": {"type": "string"},
                "date_column": {"type": "string"}, "date_start": {"type": "string"}, "date_end": {"type": "string"}
            }
        }
    },
    {"name": "consultar_tracking", "description": "Consulta rastreamento.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "consultar_documentos", "description": "Consulta CTe/NFe.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "consultar_movimentos", "description": "Consulta containers cheios.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "meta_financeiro", "description": "Lista colunas do financeiro.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "meta_tracking", "description": "Lista colunas do tracking.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "meta_documentos", "description": "Lista colunas de documentos.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "meta_movimentos", "description": "Lista colunas de movimentos.", "input_schema": {"type": "object", "properties": {}}}
]

SYSTEM_PROMPT = (
    "Você é o assistente de dados da Contrail Logística S.A.\n"
    "Use as ferramentas para consultar a API oficial. Responda sempre em português.\n"
    "Formate moedas como R$ X.XXX,XX e datas como DD/MM/AAAA."
)

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    pergunta = data.get("mensagem", "")
    historico = data.get("historico", [])

    if not pergunta:
        return jsonify({"erro": "Mensagem vazia"}), 400

    messages = historico + [{"role": "user", "content": pergunta}]

    try:
        while True:
            response = client.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages
            )

            # Processar a resposta do Claude
            content_list = []
            for block in response.content:
                if block.type == 'text':
                    content_list.append({"type": "text", "text": block.text})
                elif block.type == 'tool_use':
                    content_list.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
            
            messages.append({"role": "assistant", "content": content_list})

            if response.stop_reason == "end_turn":
                texto_final = "".join([b.text for b in response.content if b.type == 'text'])
                return jsonify({"resposta": texto_final, "historico": messages})

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        resultado = chamar_ferramenta(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": resultado
                        })
                messages.append({"role": "user", "content": tool_results})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "service": "Contrail Chat"})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)