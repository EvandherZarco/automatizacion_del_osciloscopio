import vxi11
scope = vxi11.Instrument("192.168.1.100")
print(scope.ask("*IDN?"))