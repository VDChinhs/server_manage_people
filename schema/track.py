from mongoengine import Document, StringField, DateTimeField, ReferenceField, BinaryField, IntField
import datetime

class Track(Document):
    peopleid = ReferenceField('People', null=True)
    name = StringField(required=True, max_length=50)
    zone = IntField(required=True)
    type = StringField(required=True, max_length=50)
    timestamp = DateTimeField(default=datetime.datetime.now())
    facesnapshot = BinaryField()