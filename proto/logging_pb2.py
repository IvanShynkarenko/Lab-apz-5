from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import runtime_version as _runtime_version
from google.protobuf import symbol_database as _symbol_database
from google.protobuf.internal import builder as _builder
_runtime_version.ValidateProtobufRuntimeVersion(
    _runtime_version.Domain.PUBLIC,
    6,
    31,
    1,
    '',
    'proto/logging.proto'
)
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x13proto/logging.proto\x12\x07logging\"\'\n\nLogRequest\x12\x0c\n\x04uuid\x18\x01 \x01(\t\x12\x0b\n\x03msg\x18\x02 \x01(\t\"\x19\n\x0bLogResponse\x12\n\n\x02ok\x18\x01 \x01(\x08\"\x14\n\x12GetMessagesRequest\"\'\n\x13GetMessagesResponse\x12\x10\n\x08messages\x18\x01 \x03(\t2\x93\x01\n\x0eLoggingService\x12\x37\n\nLogMessage\x12\x13.logging.LogRequest\x1a\x14.logging.LogResponse\x12H\n\x0bGetMessages\x12\x1b.logging.GetMessagesRequest\x1a\x1c.logging.GetMessagesResponseb\x06proto3')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'proto.logging_pb2', _globals)
if not _descriptor._USE_C_DESCRIPTORS:
  DESCRIPTOR._loaded_options = None
  _globals['_LOGREQUEST']._serialized_start=32
  _globals['_LOGREQUEST']._serialized_end=71
  _globals['_LOGRESPONSE']._serialized_start=73
  _globals['_LOGRESPONSE']._serialized_end=98
  _globals['_GETMESSAGESREQUEST']._serialized_start=100
  _globals['_GETMESSAGESREQUEST']._serialized_end=120
  _globals['_GETMESSAGESRESPONSE']._serialized_start=122
  _globals['_GETMESSAGESRESPONSE']._serialized_end=161
  _globals['_LOGGINGSERVICE']._serialized_start=164
  _globals['_LOGGINGSERVICE']._serialized_end=311
# @@protoc_insertion_point(module_scope)
