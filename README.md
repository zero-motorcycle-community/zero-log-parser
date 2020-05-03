# Zero Log Parser

This is a small decoder utility to parse Zero Motorcycle main bike board (MBB) and battery management system (BMS) logs.
* It is designed to emulate Zero Motorcycles' own official log parser, that turns the binary-encoded event log into a mostly-readable text file.
* If you send a log to Zero Customer Support, they should provide you with a text file in response. (Often, a phone call prompts them to process the log file)
* If you run this script, and the script does not parse some entries correctly, we can upgrade the log parser to handle them if we have a copy of Zero's official log. So try to provide us with examples like this to help the development of the parser.

## Usage
### Getting Logs
You can extract logs from the bike using the [Zero mobile app](http://www.zeromotorcycles.com/app/help/ios/):
  1. Download the Zero mobile app.
  1. Pair your motorcycle with it via bluetooth.
  1. Once the pairing is working, select `Support` > `Email bike logs`.
  1. Enter your email address into the `To:` line to send the logs to yourself rather than / in addition to Zero Motorcycles support.
  1. Open the email and download the attachment.

### Running
You'll need to install Python 3 somehow from https://www.python.org/downloads/

`$ python3 zero_log_parser.py <logfile.bin> [-o output_file]`

### Docker
If you want to run using Docker, there's only two steps: build, then run.

#### Docker Build
```
docker build . -t "zero-log-parser"
```

To explain:

`docker build`
> Build a new Docker image

`.`
 > Use a Dockerfile in the current directory ("/.")

`-t "zero-log-parser"`
 > Tag it as "zero-log-parser"

#### Docker Run

We will change directory to where the logs are, then run the tool against a log.

```
cd ~/zero-logs
docker run --rm -v "$PWD:/root" zero-log-parser /root/VIN_BMS0_2019-04-20.bin -o /root/VIN_BMS0_2019-04-20.txt
```

To explain:

`cd ~/zero-logs`
 > Go to the directory where the logs are stored.  Change this to the correct directory for you

`docker run`
 > Run a Docker image as a container

`--rm`
> Don't keep the image when it exits

`-v "$PWD:/root"`
 > Mount the current working directory as a volume inside the container at `/root`

`zero-log-parser`
 > What Docker image to run

`/root/VIN_BMS0_2019-04-20.bin`
 > Name of the binary file.  You can get this by doing an `ls` after the `cd` before this command.  Make sure to add `/root/` before it since the path in the container is different

`-o /root/VIN_BMS0_2019-04-20.txt`
> Save the decoded log file as `VIN_BMS0_2019-04-20.txt` in `/root`, which will save it in the current working directory outside of the container

## Development
Basic log documentation is at [log_structure.md](log_structure.md).

If you want to debug the script and contribute, it's helpful to be able to run the tests.
These currently look at a suite of log files and just run the parser to look for errors.

### Setup
  `$ python3 setup.py develop`

### Testing
  `$ python3 test.py <directory of log files>`

## Authors
Originally developed at https://github.com/KimBurgess/zero-log-parser

* **Kim Burgess** - *Initial Work, Inactive* - [@KimBurgess](https://github.com/KimBurgess/)
* **Brian Rice** - *Maintainer* - [@BrianTRice](https://github.com/BrianTRice/)
* **Keith Thomas** - *Contributor* - [@keithxemi](https://github.com/keithxemi)
