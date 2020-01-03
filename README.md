# Zero log parser

This is a little decoder utility to parse Zero Motorcycle main bike board (MBB) logs.

## Usage
### Getting Logs
These may be extracted from the bike using the Zero mobile app: http://www.zeromotorcycles.com/app/help/ios/
  * Download the Zero mobile app.
  * Pair your motorcycle with it via bluetooth.
  * Once the pairing is working, select `Support` > `Email bike logs` and send the logs to yourself rather than / in addition to Zero Motorcycles support.

### Running
You'll need to install Python 3 somehow from https://www.python.org/downloads/

`$ python zero_log_parser.py <*.bin file> [-o output_file]`

### Docker
If you want to run using Docker, there's only two steps: build, then run.

#### Docker Build
```
docker build . -t "zero-log-parser"
```

To explain:

`docker build` = Build a new Docker image

`.` = Use a Dockerfile in the current directory ("/.")

`-t "zero-log-parser"` = Tag it as "zero-log-parser"

#### Docker Run

We will change directory to where the logs are stored, then run the tool against a log.

```
cd ~/zero-logs
docker run --rm -v "$PWD:/root" zero-log-parser /root/VIN_BMS0_2019-04-20.bin -o /root/VIN_BMS0_2019-04-20.txt
```

To explain:

`cd ~/zero-logs` = Go to the directory where the logs are stored.  Change this to the correct directory for you

`docker run` = Run a Docker image as a container

`--rm` = Don't keep the image when it exits

`-v "$PWD:/root"` = Mount the current working directory as a volume inside the container at /root

`zero-log-parser` = What Docker image to run

`/root/VIN_BMS0_2019-04-20.bin` = Name of the binary file.  You can get this by doing an `ls` after the `cd` before this command.  Make sure to add /root/ before it since the path in the container is different

`-o /root/VIN_BMS0_2019-04-20.txt` = Save the decoded log file as VIN_BMS0_2019-04-20.txt in /root, which will save it in the current working directory outside of the container

## Development
Basic log documentation is at [log_structure.md](log_structure.md).

If you want to debug the script and contribute, it's helpful to be able to run the tests.
These currently look at a suite of log files and just run the parser to look for errors.

### Setup
  `$ python setup.py develop`

### Testing
  `$ python test.py <directory of log files>`

## Authors
Originally developed at https://github.com/KimBurgess/zero-log-parser

* **Kim Burgess** - *Initial Work, Inactive* - [@KimBurgess](https://github.com/KimBurgess/)
* **Brian Rice** - *Maintainer* - [@BrianTRice](https://github.com/BrianTRice/)
* **Keith Thomas** - *Contributor* - [@keithxemi](https://github.com/keithxemi)
