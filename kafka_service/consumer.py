from kafka import KafkaConsumer
import config
import numpy as np
import cv2

topic_name = 'cam'

c = KafkaConsumer(
    topic_name,
    group_id='group1',
    bootstrap_servers = [config.kafka_ip],
    auto_offset_reset = 'latest',
    enable_auto_commit = True
)

def decode_image(encoded_image):
    nparr = np.frombuffer(encoded_image, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    # print(image)
    return image

def save_image(image, path):
    cv2.imwrite(path, image)

for message in c:
    try:
        image = decode_image(message.value)
        # save_image(image, f"received_image_{message.offset}.png")
        # print(message.value)
        print(f"Image saved: received_image_{message.offset}.png")
    except Exception as e:
        print(f"Error decoding/saving image: {e}")