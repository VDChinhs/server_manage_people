from mongoengine import Document, StringField, BinaryField

class People(Document):
    name = StringField(required=True, max_length=50)
    role = StringField(required=True, max_length=50)
    embeddingface = BinaryField(required=True)
    facestraight = BinaryField(required=True)