import grpc

from proto import logging_pb2 as proto_dot_logging__pb2


class LoggingServiceStub(object):
    """gRPC client for LoggingService (used by facade)."""

    def __init__(self, channel):
        self.LogMessage = channel.unary_unary(
                '/logging.LoggingService/LogMessage',
                request_serializer=proto_dot_logging__pb2.LogRequest.SerializeToString,
                response_deserializer=proto_dot_logging__pb2.LogResponse.FromString,
                _registered_method=True)
        self.GetMessages = channel.unary_unary(
                '/logging.LoggingService/GetMessages',
                request_serializer=proto_dot_logging__pb2.GetMessagesRequest.SerializeToString,
                response_deserializer=proto_dot_logging__pb2.GetMessagesResponse.FromString,
                _registered_method=True)


class LoggingServiceServicer(object):
    """Base class for gRPC server; implementation in logging-service/main.py."""

    def LogMessage(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        raise NotImplementedError('Method not implemented!')

    def GetMessages(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        raise NotImplementedError('Method not implemented!')


def add_LoggingServiceServicer_to_server(servicer, server):
    handlers = {
        'LogMessage': grpc.unary_unary_rpc_method_handler(
            servicer.LogMessage,
            request_deserializer=proto_dot_logging__pb2.LogRequest.FromString,
            response_serializer=proto_dot_logging__pb2.LogResponse.SerializeToString,
        ),
        'GetMessages': grpc.unary_unary_rpc_method_handler(
            servicer.GetMessages,
            request_deserializer=proto_dot_logging__pb2.GetMessagesRequest.FromString,
            response_serializer=proto_dot_logging__pb2.GetMessagesResponse.SerializeToString,
        ),
    }
    handler = grpc.method_handlers_generic_handler('logging.LoggingService', handlers)
    server.add_generic_rpc_handlers((handler,))
    server.add_registered_method_handlers('logging.LoggingService', handlers)
