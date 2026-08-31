

class LogMessage():
    def __init__(self, content: str = "", isStartup = False):
        self.content = content
        self.isStartup = isStartup
        self.source = "log" # 'log' or 'emitter'
        self.structured = None # dict if source is 'emitter'