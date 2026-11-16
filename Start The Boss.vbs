Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\HP\StudyBuddy"
sh.Run """C:\Users\HP\AppData\Local\Programs\Python\Python310\pythonw.exe"" ""C:\Users\HP\StudyBuddy\boss.py""", 0, False
