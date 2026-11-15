# Installation

You need Node.js 24 or later and an up-to-date desktop version of Chrome or
Edge. Node.js starts a small server on your computer. You run the analysis
in your browser.

## 1. Install Node.js

Open the [Node.js download page](https://nodejs.org/en/download) and choose
a version marked **LTS** with a version number of **24 or higher**. On
Windows, run the **Windows Installer (.msi)**. On macOS, run the
**macOS Installer (.pkg)**. On Linux, follow the installation instructions
for your system on the Node.js download page.

## 2. Download and extract pXv

1. Open [pXv on GitHub](https://github.com/sschwindt/pXv) and select
   **Code** → **Download ZIP**.
2. In Downloads, extract the ZIP. On Windows, right-click it and select
   **Extract All**. On macOS, double-click it.
3. Open the extracted **pXv-main** folder. If you see another **pXv-main**
   folder inside it, open that too. You should see the `web` and `examples`
   folders.

## 3. Start the app

Open a terminal in the extracted folder. A terminal is a window where you
can type commands.

- **Windows.** In File Explorer, click the address bar at the top of the
  extracted folder. Type `powershell` and press Enter.
- **macOS or Linux.** Open Terminal and use `cd` to switch to the extracted
  folder. If you extracted it directly into Downloads, enter
  `cd ~/Downloads/pXv-main`. On macOS you can also type `cd` followed by a
  space, drag the folder from Finder into Terminal, and press Enter.

Type the following command and press Enter. The version shown should start
with `v24` or a higher number.

```sh
node --version
```

Start pXv with this command.

```sh
node web/server.mjs
```

When the terminal says **pXv running**, open
[http://127.0.0.1:8040/](http://127.0.0.1:8040/) in Chrome or Edge. Keep
the terminal open while using the app. To stop pXv, return to the terminal
and press Ctrl+C.

## 4. Check your installation

Select **Load Isar example**, wait for the video preview, then select
**Run analysis** in the **Analysis setup** panel. Follow the
[Isar River showcase](isar.md) to read and export the result.

If the terminal cannot find `node`, open a new terminal after installing
Node.js and run `node --version` again. If you see
`Cannot find module` followed by a path ending in `web/server.mjs`,
open the terminal in the extracted folder that contains `web`.
