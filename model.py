
"""
Defines the models and layers used in the NN.
Includes most layer parameters as well.
"""
import copy

import numpy as np
import tensorflow as tf
from tensorflow.keras.layers import Dense, Conv1D, Dropout, MaxPool1D, GlobalMaxPooling1D, Embedding, GlobalAveragePooling1D, Flatten
from tensorflow.keras.layers.experimental.preprocessing import Resizing


# default values for the network

embed_dim = 4
# in order NACGT, see nucleotides:
# embed_init_w = [0, 1, 0.25, 0.75, 0.5]
embed_init_w = [[1., 0., 0., 0.],
                [0., 1., 0., 0.],
                [0., 0., 1., 0.],
                [0., 0., 0., 1.],
                [1., 1., 1., 1.]]

sequence_target_length = 100
resize_interpolation = "bilinear"

conv_filters_1 = 12  # 130
conv_window_length_1 = 8
pool_size_1 = 8
dense_units_1 = 32
dense_units_2 = 64  # 196
dropout_probability = 0.5

# the loss is adjusted with a parameter for l2 regularization in each of
# the convolutional layers
penalty_regularization_w_conv = 0.001

# 2 Layer
# network.add(Conv1D(units_2, activation='relu', kernel_size=conv_window_length_2, padding='same', use_bias=True, bias_initializer=keras.initializers.Constant(bias_vector_init_value), kernel_regularizer=keras.regularizers.l2(penalty_regularization_w_conv)))
# network.add(MaxPooling2D(pool_size=pool_size_2, padding='same'))

# 3 Layer
# network.add(Conv1D(units_3, activation='relu', kernel_size=conv_window_length_3, padding='same', use_bias=True, bias_initializer=keras.initializers.Constant(bias_vector_init_value), kernel_regularizer=keras.regularizers.l2(penalty_regularization_w_conv)))
# network.add(MaxPooling2D(pool_size=pool_size_3, padding='same'))


@tf.function(input_signature=[tf.TensorSpec(shape=(None, None), dtype=tf.bool)])
def masked_lengths(mask):
    """Computes the length of each sequence using 2D mask data; essentially
    the inverse of `tf.sequence_mask`."""
    return tf.shape(mask)[1] - tf.math.argmax(mask[:, ::-1], axis=1, output_type=tf.int32)


class Resizing1D(tf.keras.layers.Layer):
    def __init__(self, length=1000, interpolation="bilinear", name="resizing1d", **kwargs):
        super(Resizing1D, self).__init__(name=name, **kwargs)
        self.resizer = Resizing(1, length, interpolation=interpolation)

    def __resize_with_length(self, t):
        sequence, length = t
        # assumes row shape: batch(1) * height(1) * width * channel:
        # tf.debugging.assert_shapes([
        #     (sequence, (1, 1, "W", "C")),
        #     (length, (1,))
        # ])
        return self.resizer(sequence[:, :, :length, :])

    @tf.function
    def __resize_node(self, input):
        # input shape : node_length * embed_dim
        x = tf.expand_dims(input, axis=0)
        x = tf.expand_dims(x, axis=0)
        # tf.debugging.assert_shapes([
        #     (x, (1, 1, "W", "C"))
        # ])
        x = self.resizer(x)
        return tf.squeeze(x, axis=[0, 1])

    @tf.autograph.experimental.do_not_convert
    def call(self, inputs, mask=None, training=None):
        # input shape : num_nodes * node_length * embed_dim
        if isinstance(inputs, tf.RaggedTensor):
            return tf.map_fn(
                self.__resize_node,
                inputs,
                fn_output_signature=tf.TensorSpec(
                    shape=[self.resizer.target_width, None],
                    dtype=tf.float32
                ),
                name="resizing_map_fn"
            )
            # add a dummy height dimension to all sequences:
        x = tf.expand_dims(inputs, axis=1)
        if mask is not None:
            lengths = masked_lengths(mask)
            # add a dummy batch dimension to all sequences:
            x = tf.expand_dims(x, axis=1)
            # output signature is expected to be same as input unless otherwise noted
            x = tf.map_fn(
                self.__resize_with_length,
                (x, lengths),
                fn_output_signature=x.dtype
            )
            # remove dummy dimensions:
            return tf.squeeze(x, axis=[1, 2])
        else:
            return tf.squeeze(self.resizer(x), axis=[1])
            # (remove dummy height dimension)

    def get_config(self):
        config = super(Resizing1D, self).get_config()
        resize_config = self.resizer.get_config()
        config.update({
            "interpolation": resize_config["interpolation"],
            "length": resize_config["width"]
        })
        return config


class CNNModel(tf.keras.Model):

    def __init__(self, voc_size=5, n_classes=2, bias_init=0.5, name="cnnmodel", **kwargs):
        super(CNNModel, self).__init__(name=name, **kwargs)
        self.n_classes = n_classes
        self.voc_size = voc_size
        # embeds integral indices into dense arrays
        # TODO also try with a one-hot encoding
        self.embed_layer = Embedding(
            voc_size,
            embed_dim,
            mask_zero=True,
            embeddings_initializer=lambda shape, dtype=None:
                tf.convert_to_tensor(
                    np.array(embed_init_w).reshape(shape),
                    dtype=dtype
                ),
            name="embed_layer"
        )

        self.resizer = Resizing1D(
            length=sequence_target_length,
            interpolation="bilinear",
            name="resizer"
        )

        self.conv1 = Conv1D(
            conv_filters_1,
            kernel_size=conv_window_length_1,
            input_shape=(None, embed_dim),
            activation='relu',
            padding='valid',
            data_format="channels_last",
            use_bias=True,
            bias_initializer=tf.keras.initializers.Constant(
                bias_init),
            kernel_regularizer=tf.keras.regularizers.l2(
                penalty_regularization_w_conv),
            name="conv1"
        )

        self.maxpool = MaxPool1D(
            pool_size=pool_size_1,
        )

        self.globalmaxpool = GlobalMaxPooling1D(name="globalmaxpool")

        self.dense1 = Dense(
            dense_units_1,
            activation='relu',
            use_bias=True,
            name="dense1"
        )

        # Rectifier Layer, with dropout
        self.dense2 = Dense(
            dense_units_2,
            activation='relu',
            use_bias=True,
            name="dense2"
        )
        self.dropout = Dropout(dropout_probability, name="dropout")

        # Classification head with softmax
        self.classifier = Dense(
            n_classes,
            activation='softmax',
            use_bias=True,
            # set the classifier bias to accelerate training:
            bias_initializer=tf.keras.initializers.Constant(
                bias_init),
            name="classifier"
        )

        self.globalaveragepool = GlobalAveragePooling1D(
            name="globalaveragepool")

    @tf.function
    def __transform_contig(self, contig, training):
        # input shape: num_nodes * node_length
        # tf.debugging.assert_shapes([
        #     (x, ("num_nodes", "node_length"))
        # ])
        # embed each integer-represented nucleotide:
        x = self.embed_layer(contig)
        # tf.debugging.assert_shapes([
        #     (x, ("num_nodes", "node_length", self.embed_layer.output_dim))
        # ])
        # resize nodes:
        x = self.resizer(x)
        # tf.debugging.assert_shapes([
        #     (x, ("num_nodes", sequence_target_length,
        #      self.embed_layer.output_dim))
        # ])
        # convolve over node lengths:
        x = self.conv1(x)
        # tf.debugging.assert_shapes([
        #     (x, ("num_nodes", "node_length_resized", self.conv1.filters))
        # ])
        x = self.maxpool(x)

        x = self.dense1(x)

        # take the max along each node:
        x = self.globalmaxpool(x)
        # tf.debugging.assert_shapes([
        #     (x, ("num_nodes", self.conv1.filters))
        # ])
        x = self.dense2(x)
        # tf.debugging.assert_shapes([
        #     (x, ("num_nodes", self.dense1.units))
        # ])
        x = self.dropout(x, training=training)
        x = self.classifier(x)
        # tf.debugging.assert_shapes([
        #     (x, ("num_nodes", self.n_classes))
        # ])
        # now vote by averaging the predictions from each node:
        # (NB: need to reshape since we took out the batch dimension)
        x = self.globalaveragepool(tf.expand_dims(x, axis=0))
        # 1 * 2
        # tf.debugging.assert_shapes([
        #     (x, (1, self.n_classes))
        # ])
        # squeezing to scrap the dummy batch dimension:
        return tf.squeeze(x)

    @ tf.autograph.experimental.do_not_convert
    def __transform_contig_train(self, contig):
        return self.__transform_contig(contig, tf.constant(True, dtype=tf.bool))

    @ tf.autograph.experimental.do_not_convert
    def __transform_contig_eval(self, contig):
        return self.__transform_contig(contig, tf.constant(False, dtype=tf.bool))

    @ tf.autograph.experimental.do_not_convert
    def call(self, inputs, training=None):
        """Returns y_pred as a `inputs.shape[0] * n_classes` tensor.

        Input shape is assumed to be a RaggedTensor of shape
        `batch_size * num_nodes * node_length`.

        Called by `__call__`, `predict`, `fit` and so on.
        """
        # build the dropout layer if need be:
        if not self.dropout.built:
            self.dropout.build((None, self.dense2.units))
        # input shape: batch_size * num_nodes * node_length
        # apply the model on each contig independently:
        if training:
            fn = self.__transform_contig_train
        else:
            fn = self.__transform_contig_eval
        return tf.map_fn(
            fn,
            inputs,
            fn_output_signature=tf.TensorSpec(shape=[2], dtype=tf.float32)
        )

    def get_config(self):
        return {
            "name": self.name,
            "n_classes": self.n_classes,
            "voc_size": self.voc_size,
            "layers": [copy.deepcopy(layer.get_config()) for layer in self.layers]
        }

    @classmethod
    def from_config(cls, config, custom_objects=None):
        model = cls(
            name=config["name"],
            voc_size=config["voc_size"],
            n_classes=config["n_classes"]
        )
        layer_configs = {
            layer_config["name"]: layer_config for layer_config in config['layers']
        }
        for idx, layer in enumerate(model.layers):
            replacement_layer = tf.keras.layers.deserialize(
                layer_configs[layer.name],
                custom_objects=custom_objects
            )
            model.layers[idx] = replacement_layer
        return model
