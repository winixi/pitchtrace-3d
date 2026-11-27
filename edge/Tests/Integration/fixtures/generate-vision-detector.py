"""重新生成同目录的测试模型，仅生成时需安装 coremltools。"""

from pathlib import Path

from coremltools.proto import FeatureTypes_pb2, Model_pb2, NeuralNetwork_pb2


model = Model_pb2.Model(specificationVersion=5)
network = model.pipeline.models.add(specificationVersion=5)
image = network.description.input.add(name="image")
image.type.imageType.width = image.type.imageType.height = 64
image.type.imageType.colorSpace = FeatureTypes_pb2.ImageFeatureType.RGB
model.description.input.add().CopyFrom(image)
nn = network.neuralNetwork
nn.arrayInputShapeMapping = NeuralNetwork_pb2.EXACT_ARRAY_MAPPING
nn.imageInputShapeMapping = NeuralNetwork_pb2.RANK4_IMAGE_MAPPING
nn.preprocessing.add(featureName="image").scaler.channelScale = 1 / 255


def layer(name, inputs=()):
    return nn.layers.add(name=name, input=inputs, output=[name])


def constant(name, rows):
    values = layer(name).loadConstantND
    values.shape.extend([len(rows), len(rows[0])])
    values.data.floatValue.extend(x for row in rows for x in row)


# 整幅白图产生人、球、无关类及低可信人框。暗背景中的白块只有在裁片中才达标。
layer("brightness", ["image"]).reduceMean.reduceAll = True
constant("scores", [[0.875, 0, 0], [0, 0.9375, 0], [0, 0, 1], [0.25, 0, 0]])
layer("raw_confidence", ["scores", "brightness"]).multiplyBroadcastable.SetInParent()
constant("raw_coordinates", [[0.25, 0.5, 0.25, 0.25], [0.625, 0.375, 0.0625, 0.0625],
                             [0.5, 0.5, 0.0625, 0.0625], [0.5, 0.75, 0.0625, 0.0625]])

nms_model = model.pipeline.models.add(specificationVersion=5)
for name, columns in [("confidence", 3), ("coordinates", 4)]:
    raw = network.description.output.add(name="raw_" + name)
    raw.type.multiArrayType.dataType = FeatureTypes_pb2.ArrayFeatureType.FLOAT32
    raw.type.multiArrayType.shape.extend([4, columns])
    nms_model.description.input.add().CopyFrom(raw)
    output = nms_model.description.output.add(name=name)
    output.type.multiArrayType.dataType = FeatureTypes_pb2.ArrayFeatureType.FLOAT32
    ranges = output.type.multiArrayType.shapeRange.sizeRanges
    ranges.add(lowerBound=0, upperBound=4)
    ranges.add(lowerBound=columns, upperBound=columns)
    model.description.output.add().CopyFrom(output)

nms = nms_model.nonMaximumSuppression
nms.pickTop.perClass = True
nms.stringClassLabels.vector.extend(["person", "sports ball", "bicycle"])
nms.confidenceInputFeatureName = "raw_confidence"
nms.coordinatesInputFeatureName = "raw_coordinates"
nms.confidenceOutputFeatureName = "confidence"
nms.coordinatesOutputFeatureName = "coordinates"
nms.confidenceThreshold = 0.125
nms.iouThreshold = 0.5
Path(__file__).with_name("vision-detector.mlmodel").write_bytes(model.SerializeToString())
