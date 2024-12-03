from modules.face.face_analyser import FaceAnalysis
import faiss
import numpy as np

FACE_ANALYSER = None

def get_face_analyser():
    global FACE_ANALYSER
    if FACE_ANALYSER is None:

        FACE_ANALYSER = FaceAnalysis('models')
        FACE_ANALYSER.prepare(ctx_id=0, det_size=(640, 640))
    return FACE_ANALYSER

def get_one_face(frame):
    face = get_face_analyser().get(frame)
    try:
        return [max(face, key=lambda face: (face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1]))]
    except ValueError:
        return None

def get_many_face(frame):
    try:
        return get_face_analyser().get(frame)
    except ValueError:
        return None
    
def verify_face(embedding_face, db_embedding, db_name):
    database_embeddings = np.array(db_embedding).astype('float32')
    faiss.normalize_L2(database_embeddings)
    index = faiss.IndexFlatIP(512)
    index.add(database_embeddings)

    k = 1
    query_embedding = np.expand_dims(embedding_face, axis=0)
    faiss.normalize_L2(query_embedding)
    distances, indices = index.search(query_embedding, k)

    confident = (1 + distances[0][0]) / 2
    if confident > 0.7:
        return db_name[indices[0][0]], confident
    else:
        return None, confident
