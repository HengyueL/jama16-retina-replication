from config import Config

from tensorflow.contrib.keras.api.keras.models import Model
from tensorflow.contrib.keras.api.keras.applications.inception_v3 import InceptionV3
from tensorflow.contrib.keras.api.keras.layers import Dense, GlobalAveragePooling2D, Average
from tensorflow.contrib.keras.api.keras.optimizers import SGD


conf = {
    'name': __name__.split('.')[-1],
    'width': 299,
    'height': 299,
    'train_dir': 'preprocessed/299/train',
    'val_dir': 'preprocessed/299/val',
    'test_dir': 'preprocessed/299/test',
    'batch_size_train': 56,
    'batch_size_test': 32,
    'augmentation_params': {
        'rescale': 1./255,
    },
    'compile_params': {
        'optimizer': SGD(lr=3e-5),
        'loss': 'binary_crossentropy',
        'metrics': ['accuracy'],
    },
    'weight_decay': 5e-4,
}

base_model = InceptionV3(weights='imagenet', include_top=False)

x = base_model.output
x = GlobalAveragePooling2D()(x)
predictions = Dense(2, activation='sigmoid')(x)
model = Model(inputs=base_model.input, outputs=predictions)

for layer in model.layers:
    layer.trainable = True

config = Config(model=model, conf=conf)


def ensemble(models):
    outputs = [model.outputs[0] for model in models]

    y = Average()(outputs)

    ensemble = Model(inputs=base_model.input, outputs=y, name='ensemble')

    return ensemble