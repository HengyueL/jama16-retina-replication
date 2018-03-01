import re
import os
import sys
import argparse
import random
import tensorflow as tf
import numpy as np
import lib.dataset
import lib.evaluation
import lib.metrics
from glob import glob

print(f"Numpy version: {np.__version__}")
print(f"Tensorflow version: {tf.__version__}")

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
random.seed(432)

# Default settings.
default_eyepacs_dir = "./data/eyepacs/bin2/test"
default_messidor2_dir = "./data/messidor2/bin2"
default_load_model_path = "./tmp/model"
default_batch_size = 32

parser = argparse.ArgumentParser(
                    description="Evaluate performance of trained graph "
                                "on test data set. "
                                "Specify --data_dir if you use the -o param.")
parser.add_argument("-m", "--messidor2", action="store_true",
                    help="evaluate performance on Messidor-2")
parser.add_argument("-e", "--eyepacs", action="store_true",
                    help="evaluate performance on EyePacs set")
parser.add_argument("-o", "--other", action="store_true",
                    help="evaluate performance on your own dataset")
parser.add_argument("--data_dir", help="directory where data set resides")
parser.add_argument("-lm", "--load_model_path",
                    help="path to where graph model should be loaded from "
                         "creates an ensemble if paths are comma separated "
                         "or a regexp",
                    default=default_load_model_path)
parser.add_argument("-b", "--batch_size",
                    help="batch size", default=default_batch_size)

args = parser.parse_args()

if bool(args.eyepacs) == bool(args.messidor2) == bool(args.other):
    print("Can only evaluate one data set at once!")
    parser.print_help()
    sys.exit(2)

if args.data_dir is not None:
    data_dir = str(args.data_dir)
elif args.eyepacs:
    data_dir = default_eyepacs_dir
elif args.messidor2:
    data_dir = default_messidor2_dir
elif args.other and args.data_dir is None:
    print("Please specify --data_dir.")
    parser.print_help()
    sys.exit(2)

load_model_path = str(args.load_model_path)
batch_size = int(args.batch_size)

# Check if the model path has comma-separated entries.
if "," in load_model_path:
    load_model_paths = load_model_path.split(",")
# Check if the model path has a regexp character.
elif any(char in load_model_path for char in '*+?'):
    load_model_paths = [".".join(x.split(".")[:-1])
                        for x in glob("{}*".format(load_model_path))]
    load_model_paths = list(set(load_model_paths))
else:
    load_model_paths = [load_model_path]

print("Found model(s):\n{}".format("\n".join(load_model_paths)))

# Other setting variables.
num_channels = 3
num_workers = 8
prefetch_buffer_size = 2 * batch_size

# Set image datas format to channels first if GPU is available.
if tf.test.is_gpu_available():
    print("Found GPU! Using channels first as default image data format.")
    image_data_format = 'channels_first'
else:
    image_data_format = 'channels_last'


all_labels = []


def feed_images(sess, x, y, test_x, test_y):
    _test_x, _test_y = sess.run([test_x, test_y])
    all_labels.append(_test_y)
    return {x: _test_x, y: _test_y}


eval_graph = tf.Graph()
with eval_graph.as_default() as g:
    # Variable for average predictions.
    avg_predictions = tf.placeholder(
        tf.float32, shape=[None, 1], name='avg_predictions')
    all_y = tf.placeholder(tf.float32, shape=[None, 1], name='all_y')

    # Get the class predictions for labels.
    predictions_classes = tf.round(avg_predictions)

    # Metrics for finding best validation set.
    tp, update_tp, reset_tp = lib.metrics.create_reset_metric(
        lib.metrics.true_positives, scope='tp', labels=all_y,
        predictions=predictions_classes)

    fp, update_fp, reset_fp = lib.metrics.create_reset_metric(
        lib.metrics.false_positives, scope='fp', labels=all_y,
        predictions=predictions_classes)

    fn, update_fn, reset_fn = lib.metrics.create_reset_metric(
        lib.metrics.false_negatives, scope='fn', labels=all_y,
        predictions=predictions_classes)

    tn, update_tn, reset_tn = lib.metrics.create_reset_metric(
        lib.metrics.true_negatives, scope='tn', labels=all_y,
        predictions=predictions_classes)

    confusion_matrix = lib.metrics.confusion_matrix(
        tp, fp, fn, tn, scope='confusion_matrix')

    brier, update_brier, reset_brier = lib.metrics.create_reset_metric(
        tf.metrics.mean_squared_error, scope='brier',
        labels=all_y, predictions=avg_predictions)

    auc, update_auc, reset_auc = lib.metrics.create_reset_metric(
        tf.metrics.auc, scope='auc',
        labels=all_y, predictions=avg_predictions)


all_predictions = []

for model_path in load_model_paths:
    # Start session.
    with tf.Session(graph=tf.Graph()) as sess:
        tf.keras.backend.set_session(sess)
        tf.keras.backend.set_learning_phase(False)
        tf.keras.backend.set_image_data_format(image_data_format)

        # Load the meta graph and restore variables from training.
        saver = tf.train.import_meta_graph("{}.meta".format(model_path))
        saver.restore(sess, model_path)

        graph = tf.get_default_graph()
        x = graph.get_tensor_by_name("x:0")
        y = graph.get_tensor_by_name("y:0")

        try:
            predictions = graph.get_tensor_by_name("predictions:0")
        except KeyError:
            predictions = graph.get_tensor_by_name("predictions/Sigmoid:0")

        # Initialize the test set.
        test_dataset = lib.dataset.initialize_dataset(
            data_dir, batch_size,
            num_workers=num_workers, prefetch_buffer_size=prefetch_buffer_size,
            image_data_format=image_data_format, num_channels=num_channels)

        # Create an iterator.
        iterator = tf.data.Iterator.from_structure(
            test_dataset.output_types, test_dataset.output_shapes)

        test_images, test_labels = iterator.get_next()

        test_init_op = iterator.make_initializer(test_dataset)

	    # Perform the evaluation.
        test_predictions = lib.evaluation.perform_test(
            sess=sess, init_op=test_init_op,
            feed_dict_fn=feed_images,
            feed_dict_args={"sess": sess, "x": x, "y": y,
                            "test_x": test_images, "test_y": test_labels},
            custom_tensors=[predictions])

        all_predictions.append(test_predictions)

    tf.reset_default_graph()

# Convert the predictions to a numpy array.
all_predictions = np.array(all_predictions)

# Calculate the linear average of all predictions.
average_predictions = np.mean(all_predictions, axis=0)

# Convert all labels to numpy array.
all_labels = np.vstack(all_labels)

# Use these predictions for printing evaluation results.
with tf.Session(graph=eval_graph) as sess:

    # Reset all streaming variables.
    sess.run([reset_tp, reset_fp, reset_fn, reset_tn, reset_brier, reset_auc])

    # Update all streaming variables with predictions.
    sess.run([update_tp, update_fp, update_fn,
              update_tn, update_brier, update_auc],
              feed_dict={avg_predictions: average_predictions,
                         all_y: all_labels})

    # Retrieve confusion matrix and estimated roc auc score.
    test_conf_matrix, test_brier, test_auc, summaries = sess.run(
        [confusion_matrix, brier, auc, summaries_op])

    # Print total roc auc score for validation.
    print(f"Brier score: {test_brier:6.4}, AUC: {test_auc:10.8}")

    # Print confusion matrix.
    print(f"Confusion matrix:")
    print(test_conf_matrix[0])

sys.exit(0)
