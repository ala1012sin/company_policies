import chromadb
import os

client = chromadb.PersistentClient(path="./chroma_data")
collection = client.get_or_create_collection(name="pdf_documents")

__all__ = ["collection", "add_pdf_chunk", "chunking_pdf"]

def add_pdf_chunk(chunk_id: str, content: str, metadata: dict):
    collection.add(
        documents=[content],
        metadatas=[metadata],
        ids=[chunk_id]
    )

def chunking_pdf(file_path: str, chunk_size: int = 500):
    from pypdf import PdfReader

    reader = PdfReader(file_path)
    chunk_id = 0

    for page_num, page in enumerate(reader.pages):
        text = page.extract_text()
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            metadata = {
                "page": page_num,
                "chunk_index": chunk_id,
                "source_file": os.path.basename(file_path)
            }
            add_pdf_chunk(f"{file_path}_chunk_{chunk_id}", chunk, metadata)
            chunk_id += 1
    print(f"Added {chunk_id} chunks from {file_path} to the collection.")

if __name__ == "__main__":
    pdf_directory = "./policies"
    for filename in os.listdir(pdf_directory):
        if filename.endswith(".pdf"):
            file_path = os.path.join(pdf_directory, filename)
            chunking_pdf(file_path)
            
    print("PDF chunking and storage complete.")
    print(f"총 collection 개수: {collection.count()}")
    n = min(3, collection.count())
    res = collection.get(limit=n, include=["documents", "metadatas"])

    for _id, doc, md in zip(res["ids"], res["documents"], res["metadatas"]):
        print("\nID:", _id)
        print("META:", md)
        print("DOC:", doc[:300], "...")
            