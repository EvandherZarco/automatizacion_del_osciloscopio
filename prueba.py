
import vxi11
inst = vxi11.Instrument('192.168.1.100')
print('NR_PT:', inst.ask('WFMPRE:NR_PT?'))
print('PT_OFF:', inst.ask('WFMPRE:PT_OFF?'))
print('RecordLength:', inst.ask('HORizontal:RECOrdlength?'))
print('Scale:', inst.ask('HORizontal:SCAle?'))
print('XINCR:', inst.ask('WFMPRE:XINCR?'))
