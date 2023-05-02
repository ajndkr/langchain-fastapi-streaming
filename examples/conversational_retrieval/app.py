from functools import lru_cache
from typing import Callable

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.templating import Jinja2Templates
from langchain.callbacks import AsyncCallbackManager
from langchain.chains import ConversationalRetrievalChain, LLMChain
from langchain.chains.question_answering import load_qa_chain
from langchain.chat_models import ChatOpenAI
from pydantic import BaseModel

from fastapi_async_langchain.responses import ConversationalRetrievalStreamingResponse
from fastapi_async_langchain.testing import mount_gradio_app
from fastapi_async_langchain.websockets import (
    ConversationalRetrievalWebsocketConnection,
)

load_dotenv()

app = mount_gradio_app(FastAPI(title="ConversationalRetrievalChainDemo"))

templates = Jinja2Templates(directory="templates")


class QueryRequest(BaseModel):
    query: str
    history: list[list[str]] | None = []


def conversational_retrieval_chain_dependency() -> (
    Callable[[], ConversationalRetrievalChain]
):
    @lru_cache(maxsize=1)
    def dependency() -> ConversationalRetrievalChain:
        from langchain.chains.conversational_retrieval.prompts import (
            CONDENSE_QUESTION_PROMPT,
        )
        from langchain.embeddings import OpenAIEmbeddings
        from langchain.vectorstores import FAISS

        db = FAISS.load_local(
            folder_path="../vector_stores/",
            index_name="langchain-python",
            embeddings=OpenAIEmbeddings(),
        )
        question_generator = LLMChain(
            llm=ChatOpenAI(
                temperature=0,
                streaming=True,
                callback_manager=AsyncCallbackManager([]),
            ),
            prompt=CONDENSE_QUESTION_PROMPT,
        )
        doc_chain = load_qa_chain(
            llm=ChatOpenAI(
                temperature=0,
                streaming=True,
                callback_manager=AsyncCallbackManager([]),
            ),
            chain_type="stuff",
        )

        return ConversationalRetrievalChain(
            combine_docs_chain=doc_chain,
            question_generator=question_generator,
            retriever=db.as_retriever(),
            return_source_documents=True,
            verbose=True,
            callback_manager=AsyncCallbackManager([]),
        )

    return dependency


conversational_retrieval_chain = conversational_retrieval_chain_dependency()


@app.post("/chat")
async def chat(
    request: QueryRequest,
    chain: ConversationalRetrievalChain = Depends(conversational_retrieval_chain),
) -> ConversationalRetrievalStreamingResponse:
    inputs = {
        "question": request.query,
        "chat_history": [(human, ai) for human, ai in request.history],
    }
    return ConversationalRetrievalStreamingResponse.from_chain(
        chain, inputs, media_type="text/event-stream"
    )


@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    chain: ConversationalRetrievalChain = Depends(conversational_retrieval_chain),
):
    connection = ConversationalRetrievalWebsocketConnection.from_chain(
        chain=chain, websocket=websocket
    )
    await connection.connect()
