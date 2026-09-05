import json;
from typing import Self;
import logging;
import os;
import yaml;


Log = logging.getLogger(__name__);

class Config(object):
    ''' 
    When instantiated directly, contains the default configuration.

    When instantiated with the fromJSON method, contains the configuration stored in the JSON file at the given file path.
    '''
    def __init__(self, data = None):
        if data == None:
            self.cfg = {};
        else:
            self.cfg = data;

    @classmethod
    def fromJSON(cls, jsonPath, default : str = None, validate : bool = True):
        return JsonConfig.from_file(jsonPath, default, validate);

    @classmethod
    def FromJSONString(cls, target : str) -> Self:
        return JsonConfig.from_string(target);
        
    @classmethod
    def from_file(cls, path, default : str = None, validate : bool = True):
        ext = os.path.splitext(path)[1].lower();
        if ext == ".yaml" or ext == ".yml":
            return YamlConfig.from_file(path, default, validate);
        else:
            return JsonConfig.from_file(path, default, validate);

    @classmethod
    def FromString(cls, target : str, format : str = "json") -> Self:
        if format != None:
            fmt = format.lower();
        if fmt == "yaml" or fmt == "yml":
            return YamlConfig.from_string(target);
        else:
            return JsonConfig.from_string(target);
        
    def GetValue(self, paramName : str, defaultValue : any):
        if paramName in self.cfg:
            Log.debug(f"Retrieved config value for '{paramName}': {self.cfg[paramName]}")
            return self.cfg[paramName];
        else:
            Log.debug(f"Config parameter '{paramName}' not found, using default value: {defaultValue}")
            return defaultValue;

    def ApplyDefaults(self, defaults, label : str = None):
        """Recursively fill in missing config options from *defaults*.

        *defaults* may be:
          - a dict   – used directly as the reference template
          - a str    – parsed as JSON first, then YAML if JSON fails
          - a Config – its .cfg dict is used

        For every key present in the defaults but absent from this config,
        a WARNING is logged and the default value is inserted.  Nested
        dicts are walked recursively so individual sub-keys are handled.

        *label* is an optional prefix used in log messages to identify
        which config file/plugin the warning belongs to.
        """
        defaults_dict = Config._resolve_defaults(defaults);
        if defaults_dict is None:
            return;
        prefix = f"[{label}] " if label else "";
        Config._apply_defaults_recursive(self.cfg, defaults_dict, path="", prefix=prefix);

    @staticmethod
    def _resolve_defaults(defaults) -> dict:
        """Convert *defaults* (str | dict | Config) to a plain dict."""
        if defaults is None:
            return None;
        if isinstance(defaults, dict):
            return defaults;
        if isinstance(defaults, Config):
            return defaults.cfg;
        if isinstance(defaults, str):
            # Try JSON first (most plugin fallbacks are JSON strings)
            try:
                return json.loads(defaults);
            except Exception:
                pass;
            # Fall back to YAML
            try:
                result = yaml.safe_load(defaults);
                return result if isinstance(result, dict) else None;
            except Exception:
                pass;
            Log.warning("ApplyDefaults: unable to parse defaults string as JSON or YAML");
            return None;
        Log.warning(f"ApplyDefaults: unsupported defaults type {type(defaults).__name__}");
        return None;

    @staticmethod
    def _apply_defaults_recursive(cfg_dict: dict, defaults_dict: dict, path: str = "", prefix: str = ""):
        """Walk *defaults_dict* and insert any keys missing from *cfg_dict*."""
        for key, default_value in defaults_dict.items():
            current_path = f"{path}.{key}" if path else key;
            if key not in cfg_dict:
                Log.warning(f"{prefix}Config option '{current_path}' is not set. Using default value: {default_value}");
                cfg_dict[key] = default_value;
            elif isinstance(default_value, dict) and isinstance(cfg_dict.get(key), dict):
                Config._apply_defaults_recursive(cfg_dict[key], default_value, current_path, prefix);

    @staticmethod
    def ValidatePropsStr(cfg : Self, target : str) -> bool:
        if target != None:
            Log.debug("Validating config properties from string")
            temp = Config.FromJSONString(target);
            if temp != None:
                result = Config.ValidateProps(cfg, temp);
                del temp;
                return result;
        Log.warning("Cannot validate properties: target string is None")
        return False;
        

    @staticmethod
    def ValidateProps(cfg : Self, target : Self) -> bool:
        Log.debug("Starting config properties validation")
        leftVars = cfg.__dict__.keys()
        rightVars = target.__dict__.keys()
        for lVar in leftVars:
            Log.debug("Props validate : " + lVar);
        Log.debug("Config properties validation completed")
        return True;


class JsonConfig(Config):
    @classmethod
    def from_file(cls, jsonPath, default : str = None, validate : bool = True):
        try:
            Log.debug(f"Attempting to load config from: {jsonPath}")
            with open(jsonPath) as file:
                config = json.load(file);
                instance = cls(config);
                if default is not None:
                    instance.ApplyDefaults(default, label=os.path.basename(jsonPath));
                Log.info(f"Successfully loaded config from: {jsonPath}")
                return instance;
        except FileNotFoundError:
            Log.warning(f"Config file not found: {jsonPath}")
            if default == None:
                return None;
            else:
                Log.info(f"Creating default config file: {jsonPath}")
                instance = cls.from_string(default);
                f = open(jsonPath, "wt")
                f.write(default)
                f.close()
                Log.info(f"Default config file created: {jsonPath}")
                return instance;
        except json.JSONDecodeError as e:
            Log.error(f"Invalid JSON in config file {jsonPath}: {e}")
            if default == None:
                return None;
            else:
                Log.info(f"Using default config due to JSON error in: {jsonPath}")
                instance = cls.from_string(default);
                f = open(jsonPath, "wt")
                f.write(default)
                f.close()
                Log.info(f"Default config file created to replace invalid JSON: {jsonPath}")
                return instance;
        except Exception as e:
            Log.error(f"Unexpected error loading config from {jsonPath}: {e}")
            if default == None:
                return None;
            else:
                instance = cls.from_string(default);
                f = open(jsonPath, "wt")
                f.write(default)
                f.close()
                return instance;

    @classmethod
    def from_string(cls, target : str) -> Self:
        if target != None:
            try:
                Log.debug("Creating config from JSON string")
                config = json.loads(target);
                instance = cls(config);
                Log.debug("Successfully created config from JSON string")
                return instance;
            except json.JSONDecodeError as e:
                Log.error(f"Invalid JSON string provided: {e}")
                return None;
            except Exception as e:
                Log.error(f"Unexpected error creating config from JSON string: {e}")
                return None;
        Log.warning("Attempted to create config from None JSON string")
        return None;


class YamlConfig(Config):
    @classmethod
    def from_file(cls, yamlPath, default : str = None, validate : bool = True):
        if yaml == None:
            Log.error("PyYAML is not installed, cannot load YAML config.");
            return None;
            
        # Automatic Migration: If YAML doesn't exist, check for JSON
        if not os.path.exists(yamlPath):
            jsonPath = yamlPath[:-5] + ".json"
            if os.path.exists(jsonPath):
                Log.info(f"Legacy JSON config found: {jsonPath}. Migrating to YAML...")
                try:
                    import json
                    with open(jsonPath, 'r') as f:
                        legacy_cfg = json.load(f)
                    with open(yamlPath, 'w') as f:
                        yaml.safe_dump(legacy_cfg, f, default_flow_style=False, sort_keys=False)
                    os.rename(jsonPath, jsonPath + ".bak")
                except Exception as e:
                    Log.warning(f"Failed to migrate legacy config {jsonPath}: {e}")

        try:
            Log.debug(f"Attempting to load config from: {yamlPath}")
            with open(yamlPath) as file:
                config = yaml.safe_load(file);
                if config == None:
                    config = {};
                instance = cls(config);
                if default is not None:
                    instance.ApplyDefaults(default, label=os.path.basename(yamlPath));
                Log.info(f"Successfully loaded config from: {yamlPath}")
                return instance;
        except FileNotFoundError:
            Log.warning(f"Config file not found: {yamlPath}")
            if default == None:
                return None;
            else:
                Log.info(f"Creating default config file: {yamlPath}")
                instance = cls.from_string(default);
                
                # Auto-convert JSON default strings to YAML for clean output
                try:
                    import json
                    parsed_default = json.loads(default)
                    default = yaml.safe_dump(parsed_default, default_flow_style=False, sort_keys=False)
                except:
                    pass

                f = open(yamlPath, "wt")
                f.write(default)
                f.close()
                Log.info(f"Default config file created: {yamlPath}")
                return instance;
        except Exception as e:
            Log.error(f"Unexpected error loading config from {yamlPath}: {e}")
            if default == None:
                return None;
            else:
                instance = cls.from_string(default);
                f = open(yamlPath, "wt")
                f.write(default)
                f.close()
                return instance;

    @classmethod
    def from_string(cls, target : str) -> Self:
        if target != None:
            if yaml == None:
                Log.error("PyYAML is not installed, cannot create config from YAML string.");
                return None;
            try:
                Log.debug("Creating config from YAML string")
                config = yaml.safe_load(target);
                if config == None:
                    config = {};
                instance = cls(config);
                Log.debug("Successfully created config from YAML string")
                return instance;
            except Exception as e:
                Log.error(f"Error creating config from YAML string: {e}")
                return None;
        Log.warning("Attempted to create config from None YAML string")
        return None;
