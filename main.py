import os
from dotenv import load_dotenv
from fastapi import FastAPI, Form, BackgroundTasks, Response
from google import genai
from google.genai import types
from supabase import create_client
from twilio.rest import Client as TwilioClient

load_dotenv()

app = FastAPI()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
twilio = TwilioClient(os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH_TOKEN"))


def get_or_create_user(telefono: str):
    result = (
        supabase.table("usuarios")
        .select("*")
        .eq("telefono", telefono)
        .execute()
    )
    if result.data:
        return result.data[0]
    nuevo = (
        supabase.table("usuarios").insert({"telefono": telefono}).execute()
    )
    return nuevo.data[0]


def procesar(telefono: str, mensaje: str):
    usuario = get_or_create_user(telefono)
    uid = usuario["id"]

    # 1. Guardar mensaje del usuario en la base de datos
    supabase.table("historial_chat").insert(
        {"usuario_id": uid, "rol": "user", "contenido": mensaje}
    ).execute()

    # 2. Recuperar el historial reciente para darle memoria a Giuseppe
    historial = (
        supabase.table("historial_chat")
        .select("*")
        .eq("usuario_id", uid)
        .order("creado_a", desc=True)
        .limit(10)  # Subimos a 10 para mejor contexto de conversación
        .execute()
    )

    # 3. Definir las instrucciones del sistema (Tu documento A)
    system_prompt = """
Eres Giuseppe, un asistente virtual nativo italiano diseñado para que un usuario hispanohablante aprenda y practique italiano útil para la vida cotidiana.

REGLAS GENERALES:
1. Utiliza un italiano sencillo, natural y de uso frecuente (Nivel por defecto: A1-A2). Prefiere términos comunes (ej. "comprare" antes que "acquistare"). Evita lo literario o excesivamente formal a menos que se te pida.
2. Ten presente que el usuario es hispanohablante. Aprovecha similitudes, pero advierte sobre falsos amigos (false friends) y errores comunes de interferencia del español.
3. Explica las dudas en ambos idiomas. Si te piden una aclaración, responde en italiano y español. Las explicaciones gramaticales se hacen principalmente en español con ejemplos en italiano.
4. Comprende el contexto. Usa el historial para interpretar la consulta. Si algo es ambiguo, cubre primero los casos más comunes. Solo pregunta si la falta de información cambia drásticamente la traducción o puede inducir a un error grave.
5. Enseña italiano real. Indica si una expresión es formal, informal, regional o poco natural. Prioriza lo más frecuente.

PRINCIPIO DE DESAMBIGUACIÓN:
Antes de preguntar al usuario qué quiere decir (ej. con verbos multiuso como "llevar"), evalúa: ¿Puedo responder cubriendo las opciones más probables directamente? Si la diferencia solo añade precisión, responde primero con lo más común y menciona las alternativas. Si cambia totalmente la palabra, pide aclaración.
"""

    # 4. Formatear el historial para el SDK de Gemini (En orden cronológico correcto)
    chat_history = [
        types.Content(
            role=msg["rol"], parts=[types.Part(text=msg["contenido"])]
        )
        for msg in reversed(historial.data)
    ]

    # 5. Generar la respuesta usando gemini-2.5-flash-lite
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        config=types.GenerateContentConfig(system_instruction=system_prompt),
        contents=chat_history,
    )
    respuesta = response.text

    # 6. Guardar la respuesta de Giuseppe en la base de datos
    supabase.table("historial_chat").insert(
        {"usuario_id": uid, "rol": "model", "contenido": respuesta}
    ).execute()

    # 7. Enviar la respuesta al usuario vía WhatsApp (Twilio)
    twilio.messages.create(
        from_=os.getenv("TWILIO_WHATSAPP_NUMBER"),
        body=respuesta,
        to=f"whatsapp:{telefono}",
    )


@app.post("/webhook")
async def webhook(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
):
    telefono = From.replace("whatsapp:", "")
    background_tasks.add_task(procesar, telefono, Body)
    return Response(
        content="<Response></Response>", media_type="application/xml"
    )