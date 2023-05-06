import os
import logging
import argparse
from pyspark.sql import SparkSession
from pyspark.sql.functions import lit, current_timestamp, row_number
from pyspark.sql.window import Window
from pyspark import SparkConf
import os

def start_spark_history_server(log_dir,event_dir):
    """
    Launches a Spark History Server and configures it to read logs from the specified directory.
    """
    # Set Spark configuration
    conf = SparkConf().setAppName("SparkHistoryServer").set("spark.eventLog.enabled", "true") \
                      .set("spark.eventLog.dir", event_dir).set("spark.history.fs.logDirectory", log_dir)

    # Create a SparkSession
    spark = SparkSession.builder.config(conf=conf).getOrCreate()

    # Start Spark History Server
    os.system(f"nohup spark-submit --class org.apache.spark.deploy.history.HistoryServer \
            $SPARK_HOME/jars/spark-*.jar > /dev/null 2>&1 &")

    return spark
class IngestionJob:
    def __init__(self, spark, log_file):
        self.spark = spark

        # Initialize logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

        # Set up file handler
        file_handler = logging.FileHandler(log_file)

        # Set log message format
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        file_handler.setFormatter(formatter)

        # Add file handler to logger
        self.logger.addHandler(file_handler)

    def ingest_csv_to_deltalake(self, file_path, output_path):
        # Reading CSV files with header
        #df = self.spark.read.option("header", True).option("inferSchema", True).csv(file_path)
        df = self.spark.read.csv(file_path, header=True, inferSchema=True, sep='|')
        print(file_path)
        self.logger.info(f"Read CSV file with {df.count()} rows from {file_path}")

        # Reading CSV files without header
        if len(df.columns) == 1:
            df = self.spark.read.option("header", False).option("inferSchema", True).csv(file_path)
            df = df.selectExpr("_c0 as id")
        if "id" not in df.columns:
            df = df.withColumn("id", lit(None).cast("string"))

        # Add batch_id and current timestamp columns
        df = df.withColumn("timestamp", current_timestamp())
        window = Window.orderBy("timestamp")
        df = df.withColumn("batch_id", row_number().over(window))
        self.logger.info(f"Added batch_id and timestamp columns to DataFrame")

        # Write to Delta Lake with append mode and partition by batch_id and timestamp
        timestamp = df.select("timestamp").collect()[0][0].strftime('%Y-%m-%d-%H-%M-%S')
        output_table = f"{output_path}/batch_{df.select('batch_id').collect()[0][0]}_{timestamp}"
        df.write.format("delta").mode("append").partitionBy("batch_id", "timestamp").save(output_table)
        self.logger.info(f"Wrote {df.count()} rows to Delta Lake at {output_table}")

if __name__ == "__main__":
    # Initialize SparkSession
    spark = SparkSession.builder.appName("IngestionJob").getOrCreate()

    # Parse arguments
    parser = argparse.ArgumentParser(description='Ingest CSV files into Delta Lake')
    parser.add_argument("--data_path", help="Path to csv files", required=True)
    parser.add_argument('--output_path', type=str, default='delta', help='Output path for Delta Lake table')
    parser.add_argument('--log_file', type=str, default='ingestion.log', help='Log file path')
    parser.add_argument('--event_dir', type=str, default='/events', help='Event directory')
    args = parser.parse_args()

    # Initialize IngestionJob
    job = IngestionJob(spark, args.log_file)

    files = os.listdir(args.data_path)
    file_paths = [file for file in files if file.endswith('.csv')]
    # Process each CSV file
    for file_path in file_paths:
        job.ingest_csv_to_deltalake(args.data_path + "/" + file_path, args.output_path)

    # Stop SparkSession
    spark.stop()

    spark = start_spark_history_server(args.log_file,args.event_dir)